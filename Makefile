# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

RUN := uv run

all: build

build:
	uv build

setup:
	${RUN} pre-commit install

pre-commit:
	${RUN} pre-commit run --all-files

format:
	${RUN} ruff format

lint:
	${RUN} ruff check --fix

clean:
	rm -rf \
		build/ \
		dist/ \
		*.egg-info/ \
		__pycache__/ \
		kischvidimer/__picache__/ \
		kischvidimer/_version.py \


.PHONY: \
	all \
	build \
	clean \
	format \
	lint \
	pre-commit \
	setup \
