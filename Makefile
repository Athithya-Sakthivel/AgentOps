temp-s3:
	ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text); \
	BUCKET=s3-temp-bucket-dataops-$$ACCOUNT_ID-xyz; \
	REGION=$$AWS_REGION; \
	if ! aws s3api head-bucket --bucket $$BUCKET 2>/dev/null; then \
		aws s3api create-bucket \
			--bucket $$BUCKET \
			--region $$REGION \
			--create-bucket-configuration LocationConstraint=$$REGION; \
		echo "Created $$BUCKET"; \
	else \
		echo "Bucket $$BUCKET already exists"; \
	fi

delete-temp-s3:
	ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text); \
	BUCKET=s3-temp-bucket-dataops-$$ACCOUNT_ID-xyz; \
	REGION=$$AWS_REGION; \
	if aws s3api head-bucket --bucket $$BUCKET 2>/dev/null; then \
		aws s3 rm s3://$$BUCKET --recursive; \
		aws s3api delete-bucket --bucket $$BUCKET --region $$REGION; \
		echo "Deleted $$BUCKET"; \
	else \
		echo "Bucket $$BUCKET does not exist"; \
	fi

tree:
	tree -a -I '.venv|.repos|.ruff_cache|archive|.git'

push:
	git add .
	git commit -m "new"
	git push origin main

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.log" ! -path "./.git/*" -delete
	find . -type f -name "*.pulumi-logs" ! -path "./.git/*" -delete
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	rm -rf logs
	rm -rf src/terraform/.plans
	clear

test-infra:
	bash src/infra/run.sh --env staging --create || true && bash src/infra/run.sh --destroy --env staging --yes-delete || true && \
	bash src/infra/run.sh --env staging --create