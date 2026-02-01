#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

TEST_PORT=6969
export TEST_PORT

PYTHON_VENV_DIR="packages/python/.venv"
PYTHON_PACKAGE_DIR="packages/python"
JS_CLIENT_DIR="packages/js/client"
SERVER_URL="http://localhost:$TEST_PORT"
UVICORN_APP_PATH="packages.python.tests.fixtures.server:app"

run_quiet() {
    out=$(mktemp)
    "$@" >"$out" 2>&1 || cat "$out"
    rm -f "$out"
}

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT

if [ ! -d "$PYTHON_VENV_DIR" ]; then
    python3 -m venv "$PYTHON_VENV_DIR"
    source "$PYTHON_VENV_DIR/bin/activate"
    pip install -e "$PYTHON_PACKAGE_DIR/.[test,server]"
else
    source "$PYTHON_VENV_DIR/bin/activate"
    run_quiet pip install -e "$PYTHON_PACKAGE_DIR/.[test,server]"
fi

echo -e "${YELLOW}Running Python Unit Tests...${NC}"
pytest "$PYTHON_PACKAGE_DIR/tests/unit_test.py"
pytest "$PYTHON_PACKAGE_DIR/tests/cli_test.py"
echo -e "${GREEN}Python Unit Tests Passed.${NC}"

cd "$JS_CLIENT_DIR"

if [ ! -d "node_modules" ]; then
    npm install
fi

echo -e "${YELLOW}Running JavaScript Unit Tests...${NC}"
npm test -- --run src/tests/client.test.ts
echo -e "${GREEN}JavaScript Unit Tests Passed.${NC}"

cd - > /dev/null

uvicorn $UVICORN_APP_PATH \
  --port $TEST_PORT &
SERVER_PID=$!

echo -e "${YELLOW}Waiting for FastAPI server...${NC}"
for i in {1..20}; do
    if curl --output /dev/null --silent "$SERVER_URL"; then
        break
    else
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo -e "${RED}FastAPI server crashed.${NC}"
            cat "$UVICORN_LOG"
            exit 1
        fi
        sleep 1
    fi
    if [ "$i" -eq 20 ]; then
        echo -e "${RED}FastAPI server failed to start within 20 seconds.${NC}"
        exit 1
    fi
done

echo -e "${GREEN}Running Python Integration Tests...${NC}"
pytest "$PYTHON_PACKAGE_DIR/tests/integration_test.py"
echo -e "${GREEN}Python Integration Tests Passed.${NC}"

echo -e "${GREEN}Running JavaScript Integration Tests...${NC}"
cd "$JS_CLIENT_DIR"
npm test -- --run src/tests/integration.test.ts
echo -e "${GREEN}JavaScript Integration Tests Passed.${NC}"
cd - > /dev/null

echo -e "${GREEN}All tests passed successfully!${NC}"