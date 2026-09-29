#!/bin/bash
# TEMPORARY diagnostic (Kraft-r3v1n), reverted before review.
docker version --format 'docker {{.Server.Version}}'
docker info --format 'cgroup v{{.CgroupVersion}} {{.CgroupDriver}}'
containerd --version || true
docker pull -q alpine:3 >/dev/null
trial() {  # $1 name, $2 prefix
  n=oomprobe-$1
  docker run --init --log-driver=none --memory=32m --name "$n" alpine:3 sh -c "$2"'x=a; while true; do x="$x$x"; done' >/dev/null 2>&1
  rc=$?
  f=false
  for i in $(seq 50); do
    f=$(docker inspect -f '{{.State.OOMKilled}}' "$n")
    [ "$f" = true ] && break
    sleep 0.1
  done
  echo "rc=$rc flag=$f"
  docker rm -f "$n" >/dev/null
}
export -f trial
for arm in instant delayed; do
  pre=""
  [ $arm = delayed ] && pre="sleep 1; "
  echo "== $arm"
  seq "${TRIALS:-120}" | xargs -P 8 -I{} bash -c "trial $arm-{} '$pre'" | sort | uniq -c
done
