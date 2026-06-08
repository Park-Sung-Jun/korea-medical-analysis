"""
Vercel API 로 dist/ 정적 배포 (CLI 불필요).
파일별 sha1 업로드(/v2/files) → 배포 생성(/v13/deployments, target=production) → READY 폴링.
환경변수: VERCEL_TOKEN (필수), VERCEL_TEAM (team_... 권장), VERCEL_PROJECT (이름).
"""
import os, json, time, hashlib
from pathlib import Path
import requests
import _env  # noqa: F401  (.env 자동 로드 side-effect)

TOKEN = os.environ["VERCEL_TOKEN"]
TEAM = os.environ.get("VERCEL_TEAM", "").strip()
NAME = os.environ.get("VERCEL_PROJECT", "korea-medical-access").strip()
DIST = Path(__file__).resolve().parent.parent / "dist"

H = {"Authorization": "Bearer " + TOKEN}
PARAMS = {"teamId": TEAM} if TEAM else {}
SKIP = {"netlify.toml"}


def main():
    files = []
    for p in sorted(DIST.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(DIST).as_posix()
        if rel in SKIP:
            continue
        b = p.read_bytes()
        sha = hashlib.sha1(b).hexdigest()
        r = requests.post("https://api.vercel.com/v2/files", params=PARAMS,
                          headers={**H, "Content-Type": "application/octet-stream",
                                   "x-vercel-digest": sha},
                          data=b, timeout=90)
        if r.status_code not in (200, 201):
            raise SystemExit(f"파일 업로드 실패 {rel}: {r.status_code} {r.text[:200]}")
        files.append({"file": rel, "sha": sha, "size": len(b)})
        print(f"  uploaded {rel} ({len(b)} B)")

    body = {
        "name": NAME,
        "files": files,
        "projectSettings": {"framework": None},
        "target": "production",
    }
    r = requests.post("https://api.vercel.com/v13/deployments", params=PARAMS,
                      headers={**H, "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=120)
    d = r.json()
    if r.status_code not in (200, 201):
        raise SystemExit(f"배포 생성 실패: {r.status_code} {json.dumps(d, ensure_ascii=False)[:400]}")
    did, url = d.get("id"), d.get("url")
    print(f"배포 생성: id={did} url=https://{url} state={d.get('readyState')}")

    for _ in range(50):
        time.sleep(3)
        s = requests.get(f"https://api.vercel.com/v13/deployments/{did}", params=PARAMS,
                         headers=H, timeout=30).json()
        st = s.get("readyState") or s.get("status")
        print(f"  state={st}")
        if st == "READY":
            print("\n=== 배포 완료 ===")
            print(f"LIVE URL : https://{url}")
            alias = s.get("alias") or []
            for a in alias:
                print(f"ALIAS    : https://{a}")
            return
        if st in ("ERROR", "CANCELED"):
            raise SystemExit(f"배포 실패: {json.dumps(s.get('error'), ensure_ascii=False)}")
    raise SystemExit("READY 폴링 시간 초과")


if __name__ == "__main__":
    main()
