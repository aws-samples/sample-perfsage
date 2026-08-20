.PHONY: test test-local test-bedrock setup deploy destroy invoke

# Local testing (no AWS needed)
test-local:
	source .venv/bin/activate && python3 tests/test_local.py

# Integration testing (needs AWS creds + strands-agents)
test-bedrock:
	source .venv/bin/activate && python3 tests/test_with_bedrock.py

# Alias
test: test-local

# Setup local dev environment
setup:
	python3 -m venv .venv
	source .venv/bin/activate && pip install pyyaml faker jsonref
	@echo "Local env ready. Run 'make test-local' to verify."

# Setup with full deps (for Bedrock testing)
setup-full:
	python3 -m venv .venv
	source .venv/bin/activate && pip install -r src/testgen_agent/requirements.txt
	@echo "Full env ready. Run 'make test-bedrock' to test with Bedrock."

# CDK deploy (from unified infra folder)
deploy:
	cd ../infra && pip install -r requirements.txt && cdk deploy --all -c env=dev

# CDK destroy
destroy:
	cd ../infra && cdk destroy --all -c env=dev --force

# CDK diff
diff:
	cd ../infra && cdk diff -c env=dev

# Test via Lambda console invoke (safe, no open endpoints)
invoke:
	@echo "Invoking Lambda directly via AWS CLI..."
	aws lambda invoke --function-name perfsage-testgen-dev --region us-east-1 \
		--payload "$$(echo '{"body": "{\"spec\": \"openapi: 3.0.3\\ninfo:\\n  title: Test\\n  version: 1.0\\npaths:\\n  /health:\\n    get:\\n      responses:\\n        200:\\n          description: OK\", \"prompt\": \"Smoke test with 5 users\", \"format\": \"yaml\"}"}' | base64)" \
		--cli-binary-format raw-in-base64-out \
		/tmp/lambda-response.json && cat /tmp/lambda-response.json | python3 -m json.tool

# Test via API Gateway (requires valid AWS credentials in the request)
test-api:
	@echo "Testing via API Gateway: $(API_ENDPOINT)"
	curl -s -X POST "$(API_ENDPOINT)jobs" \
		-H "Content-Type: application/json" \
		-d '{"spec": "openapi: 3.0.3\ninfo:\n  title: Test\n  version: 1.0\npaths:\n  /health:\n    get:\n      responses:\n        200:\n          description: OK", "prompt": "Smoke test with 5 users for 1 minute", "format": "yaml"}' | python3 -m json.tool
