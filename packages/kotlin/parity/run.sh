#!/bin/bash

# i'm sure we don't need such a complex script

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SRC="$REPO/packages/kotlin/ephaptic-android/src/main/java/com/ephaptic/android"
VENV="$REPO/packages/python/.venv/bin/python"
OUT="$HERE/.out"
LOG="$HERE/.server.log"
PORT="${1:-${KT_PORT:-7900}}"

GC="$HOME/.gradle/caches/modules-2/files-2.1"

if [ ! -d "$GC" ]; then
    echo "The Gradle cache is absent. Run ./gradlew build in packages/kotlin once first."
    exit 2
fi

jar() { find "$GC" -path "*$1*" -name "$2" 2>/dev/null | grep -v sources | head -1; }

KOTLINC=$(jar org.jetbrains.kotlin/kotlin-compiler-embeddable/2.1.0 'kotlin-compiler-embeddable-2.1.0.jar')
STDLIB=$(jar org.jetbrains.kotlin/kotlin-stdlib/2.1.0 'kotlin-stdlib-2.1.0.jar')
REFLECT=$(jar org.jetbrains.kotlin/kotlin-reflect/2.0.21 'kotlin-reflect-2.0.21.jar')
DAEMON=$(jar kotlin-daemon-embeddable 'kotlin-daemon-embeddable-2*.jar')
TROVE=$(jar trove4j 'trove4j-*.jar')
ANNOT=$(jar 'org.jetbrains/annotations/13.0' 'annotations-13.0.jar')
MOSHIPACK=$(jar moshipack 'moshipack-1.0.1.jar')
MOSHI=$(jar 'com.squareup.moshi/moshi/1.15.0' 'moshi-1.15.0.jar')
MOSHIKOTLIN=$(jar 'moshi-kotlin/1.15.0' 'moshi-kotlin-1.15.0.jar')
OKHTTP=$(jar 'okhttp/4.12.0' 'okhttp-4.12.0.jar')
OKIO=$(jar 'okio-jvm/3.10.2' 'okio-jvm-3.10.2.jar')
COROUTINES=$(jar 'kotlinx-coroutines-core-jvm/1.10.2' 'kotlinx-coroutines-core-jvm-1.10.2.jar')

for name in KOTLINC STDLIB REFLECT DAEMON TROVE ANNOT MOSHIPACK MOSHI MOSHIKOTLIN OKHTTP OKIO COROUTINES; do
    if [ -z "${!name}" ]; then
        echo "Could not resolve $name from the Gradle cache. Run ./gradlew build in packages/kotlin first."
        exit 2
    fi
done

LAUNCH="$KOTLINC:$STDLIB:$DAEMON:$TROVE:$ANNOT:$COROUTINES"
CP="$STDLIB:$REFLECT:$MOSHIPACK:$MOSHI:$MOSHIKOTLIN:$OKHTTP:$OKIO:$COROUTINES"

cleanup() {
    [ -n "${PY_PID:-}" ] && kill "$PY_PID" 2>/dev/null
}
trap cleanup EXIT

echo "Compiling the client and harness..."
rm -rf "$OUT"
mkdir -p "$OUT"
java -cp "$LAUNCH" org.jetbrains.kotlin.cli.jvm.K2JVMCompiler \
    -classpath "$CP" -nowarn -d "$OUT" \
    "$SRC/EphapticClient.kt" \
    "$SRC/internal/Protocol.kt" \
    "$HERE/AndroidLogStub.kt" \
    "$HERE/Harness.kt" 2>&1 \
    | grep -Ev "^warning: |is deprecated|^ *\^ *$|only be used with the compiler argument"

if [ ! -f "$OUT/harness/HarnessKt.class" ]; then
    echo "Compilation failed."
    exit 1
fi

if [ ! -x "$VENV" ]; then
    echo "The Python venv is absent. Run ./tests.sh once to create it."
    exit 2
fi

echo "Starting the Python server on port $PORT..."
KT_PORT="$PORT" "$VENV" "$HERE/server.py" > "$LOG" 2>&1 &
PY_PID=$!
for _ in $(seq 1 60); do
    grep -q READY "$LOG" 2>/dev/null && break
    sleep 0.25
done
if ! grep -q READY "$LOG" 2>/dev/null; then
    echo "The server did not start:"
    cat "$LOG"
    exit 1
fi

echo "Driving it with the Kotlin client..."
java --add-exports java.base/sun.reflect.generics.reflectiveObjects=ALL-UNNAMED \
    -cp "$OUT:$CP" harness.HarnessKt "$PORT"
