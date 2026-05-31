# In a separate terminal or background
cd /workspace/src/workloads/mcp-server
source .venv/bin/activate
export PORT=8001
export DATABASE_URL="postgresql://agentops:localdev@localhost:5432/kestral"
python3 src/main.py > /tmp/mcp-server.log 2>&1 &

sleep 3
curl -s http://localhost:8001/readyz

cd /workspace/src/workloads/agent-service
source .venv/bin/activate
export PORT=8000
export MCP_SERVER_URL="http://localhost:8001/mcp"
export DATABASE_URL="postgresql://agentops:localdev@localhost:5432/kestral"
export JWT_PRIVATE_KEY_PEM="$(python3 -c 'from joserfc.jwk import ECKey; key = ECKey.generate_key("P-256"); print(key.as_pem().decode())')"
export SESSION_SECRET="dev-secret"
export ADMIN_ALLOWED_GOOGLE_DOMAINS='["gmail.com"]'
export ADMIN_ALLOWED_MICROSOFT_TENANTS='["b8a65a11-9d1c-4d48-b9f6-dabfc67f2e4b"]'
python3 src/main.py > /tmp/agent-service.log 2>&1 &
sleep 5
curl -s http://localhost:8000/healthz