.PHONY: sandbox run test
sandbox:
	docker build -t glassbox-sandbox:latest sandbox/
run:
	python -m uvicorn --factory app.main:get_app --env-file .env --port 8000
test:
	pytest --cov=app --cov-report=term-missing
