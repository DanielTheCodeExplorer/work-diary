.PHONY: build-WorkDiaryFunction

# Keep the Lambda package deliberately small. The repository also contains the
# local server, browser assets, tests, and a SQLite database; none belongs in
# the cloud runtime artifact.
build-WorkDiaryFunction:
	python3.12 -m pip wheel --no-deps --wheel-dir "$(ARTIFACTS_DIR)/.build-wheels" http-ece==1.2.1
	python3.12 -m pip install --requirement requirements.txt --target "$(ARTIFACTS_DIR)" --find-links "$(ARTIFACTS_DIR)/.build-wheels" --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --abi cp312 --only-binary=:all: --disable-pip-version-check
	rm -r "$(ARTIFACTS_DIR)/.build-wheels"
	cp lambda_backend.py task_schedule.py integration_security.py "$(ARTIFACTS_DIR)/"
