#!/usr/bin/env bash
set -euo pipefail
repo=$(pwd)
build=$(mktemp -d)
mkdir -p "$build/cereal/gen/cpp"
(
  cd openpilot/cereal
  capnp compile -I . -I include -I "$repo/opendbc_repo/opendbc/car" -I "$repo/opendbc_repo/opendbc/car/include" \
    -oc++:"$build/cereal/gen/cpp" log.capnp custom.capnp deprecated.capnp
)
(
  cd opendbc_repo/opendbc/car
  capnp compile -I . -I include -oc++:"$build/cereal/gen/cpp" car.capnp
)
g++ -std=c++17 -O1 -I "$repo/openpilot" -I "$build" -I "$build/cereal/gen/cpp" \
  tools/naver_ci/wire_probe.cc \
  openpilot/selfdrive/carrot/realtime/compact_state_native.cc \
  "$build"/cereal/gen/cpp/*.capnp.c++ -lcapnp -lkj -o "$build/wire-probe"
echo "NAVER_WIRE_PROBE=$build/wire-probe" >> "$GITHUB_ENV"
