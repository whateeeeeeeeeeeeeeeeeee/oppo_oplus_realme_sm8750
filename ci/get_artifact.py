#!/usr/bin/env python3
"""
get_artifact.py — 下载 GitHub Actions 工件（支持断点续传重试）。

背景：大工件直连下载容易超时，用 Range 续传。
用法:
  python3 get_artifact.py <run_id> [输出目录]
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = 'whateeeeeeeeeeeeeeeeeee/oppo_oplus_realme_sm8750'
TOKEN = open('/home/Gold/.gh_kernel_token').read().strip()


def call(path):
    req = urllib.request.Request(
        path if path.startswith('http') else 'https://api.github.com' + path)
    req.add_header('Authorization', 'token ' + TOKEN)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'dsh')
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    run_id = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else 'artifacts'
    os.makedirs(outdir, exist_ok=True)

    arts = call('/repos/%s/actions/runs/%s/artifacts' % (REPO, run_id))
    items = arts.get('artifacts') or []
    if not items:
        print('该 run 没有工件（可能还在跑或失败）')
        return 1
    print('找到 %d 个工件:' % len(items))
    for a in items:
        print('   %-28s %10d 字节  过期 %s'
              % (a['name'], a['size_in_bytes'], a['expires_at'][:10]))

    for a in items:
        url = a['archive_download_url']
        dest = os.path.join(outdir, '%s.zip' % a['name'])
        print('\n>>> 下载 %s ...' % a['name'])
        # 用 curl 以支持断点续传
        for attempt in range(1, 6):
            cmd = ['curl', '-sSL', '--fail', '-C', '-', '-o', dest,
                   '-H', 'Authorization: token ' + TOKEN, url]
            r = subprocess.run(cmd, capture_output=True, text=True)
            size = os.path.getsize(dest) if os.path.exists(dest) else 0
            if r.returncode == 0 and size > 0:
                print('    OK  %s (%d 字节)' % (dest, size))
                break
            print('    第 %d 次失败 (rc=%s, 已下 %d 字节)，重试…'
                  % (attempt, r.returncode, size))
            if attempt == 5:
                print('    !! 放弃')
                return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
