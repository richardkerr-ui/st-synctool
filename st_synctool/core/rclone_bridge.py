import json, subprocess, socket, getpass
from datetime import datetime, timezone

RCLONE_BIN = "rclone"

def _run(args, timeout=300):
    return subprocess.run([RCLONE_BIN]+args, capture_output=True, text=True, timeout=timeout)

def lsjson(remote_path, with_checksum=True):
    args = ["lsjson","--recursive"]
    if with_checksum: args.append("--checksum")
    args.append(remote_path)
    r = _run(args, timeout=600)
    if r.returncode != 0: raise RuntimeError(f"rclone lsjson failed: {r.stderr}")
    return json.loads(r.stdout)

def lsjson_to_manifest(remote_path, label="server"):
    items = lsjson(remote_path, with_checksum=True)
    files = {}
    for item in items:
        if item.get("IsDir"): continue
        cs = {}
        if "Hashes" in item:
            h = item["Hashes"]
            if "SHA-256" in h: cs["sha256"]     = h["SHA-256"].lower()
            if "xxhash"  in h: cs["xxhash3_64"] = h["xxhash"].lower()
            if "MD5"     in h: cs["md5"]         = h["MD5"].lower()
        files[item["Path"]] = {"type":"file","size":item.get("Size",0),
                               "modtime":item.get("ModTime",""),"checksums":cs}
    return {"schema_version":"1.0","created_at":datetime.now(timezone.utc).isoformat(),
            "label":label,"root":remote_path,"workstation":socket.gethostname(),
            "user":getpass.getuser(),"file_count":len(files),"files":files,
            "total_size_bytes":sum(v["size"] for v in files.values())}

def sync(src, dst, dry_run=False, log_cb=None):
    args = ["sync", src, dst, "--progress"]
    if dry_run: args.append("--dry-run")
    r = _run(args, timeout=3600)
    if log_cb:
        for line in r.stdout.splitlines(): log_cb(line,"info")
        for line in r.stderr.splitlines(): log_cb(line,"warning" if r.returncode==0 else "error")
    return r.returncode == 0
