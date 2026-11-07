ANDROID_API_OUT ?= packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated
ANDROID_API_PACKAGE ?= dev.livachat.protocol.generated

.PHONY: generate-android-api check-android-api-generated
generate-android-api:
	python3 scripts/generate_kotlin_api.py --schema packages/api-schema --out $(ANDROID_API_OUT) --package $(ANDROID_API_PACKAGE)

check-android-api-generated: generate-android-api
	test -z "$$(git status --porcelain -- $(ANDROID_API_OUT))"

# --- iOS protocol DTO (Swift) ---

SWIFT_API_OUT ?= packages/sdk-swift/Sources/LivaChatProtocol/Generated
IOS_SIMULATOR ?= iPhone 16 Pro
# Pin the newest runtime that actually has IOS_SIMULATOR; otherwise xcodebuild
# resolves `name` against the newest installed runtime, which fails when the
# device only exists on an older one. Override with e.g. IOS_SIMULATOR_OS=18.0.
IOS_SIMULATOR_OS ?= $(shell python3 scripts/ios_simulator_os.py "$(IOS_SIMULATOR)" 2>/dev/null)
IOS_DESTINATION = platform=iOS Simulator,name=$(IOS_SIMULATOR)
ifneq ($(IOS_SIMULATOR_OS),)
IOS_DESTINATION := $(IOS_DESTINATION),OS=$(IOS_SIMULATOR_OS)
endif

.PHONY: generate-swift-api check-swift-api-generated swift-protocol-test \
        swift-sdk-test swift-sdk-ios-test build-ios ios-i18n-check ios-app-test \
        ios-app-uitest

generate-swift-api:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT)

check-swift-api-generated:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT) --check

swift-protocol-test:
	cd packages/sdk-swift && swift test

# Host-side SDK tests (pure logic; native processors use fakes).
swift-sdk-test:
	cd apps/ios/LivaChatSDK && swift test

# Simulator tests (native Opus/VAD/JPEG + E2E). The package test scheme is
# named after the package, not the product.
swift-sdk-ios-test:
	cd apps/ios/LivaChatSDK && xcodebuild test \
	  -scheme LivaChatSDK \
	  -destination '$(IOS_DESTINATION)'

build-ios:
	./scripts/build-ios.sh

ios-i18n-check:
	python3 scripts/i18n/check_ios_localization.py

ios-app-test:
	xcodebuild test \
	  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
	  -scheme LivaChatApp \
	  -destination '$(IOS_DESTINATION)' \
	  -only-testing:LivaChatAppTests

# Slow UI suite (RTL, Dynamic Type, shells). Not part of ci-mobile's push job;
# run manually or via the workflow_dispatch job.
ios-app-uitest:
	xcodebuild test \
	  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
	  -scheme LivaChatApp \
	  -destination '$(IOS_DESTINATION)' \
	  -only-testing:LivaChatAppUITests

# --- mock server ---

MOCK_SERVER_DIR := tools/local-dev/mock-services/livachat_mock_server
MOCK_HOST ?= 0.0.0.0
MOCK_PORT ?= 18080
MOCK_SCENARIO ?= default

.PHONY: mock-server mock-server-install mock-server-test

mock-server-install:
	pip install -e "packages/sdk-python"
	pip install -e "$(MOCK_SERVER_DIR)[test]"

mock-server:
	cd $(MOCK_SERVER_DIR) && MOCK_SCENARIO=$(MOCK_SCENARIO) \
		uvicorn livachat_mock_server.app:create_app --factory --host $(MOCK_HOST) --port $(MOCK_PORT)

mock-server-test:
	cd $(MOCK_SERVER_DIR) && pytest tests/ -v

# --- real server ---

.PHONY: server-install server-dev server-test server-infra-up server-infra-down

server-install:
	.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"

server-dev:
	cd services && python app/main.py

server-test:
	cd services && pytest -q

server-infra-up:
	docker compose -f infra/docker/docker-compose.dev.yml up -d

server-infra-down:
	docker compose -f infra/docker/docker-compose.dev.yml down -v

# --- mylife web app ---
# Reference only: no longer developed or maintained.

MYLIFE_DIR := apps/web/mylife

.PHONY: mylife-install mylife-dev mylife-build mylife-check

mylife-install:
	cd $(MYLIFE_DIR) && npm install

mylife-dev:
	cd $(MYLIFE_DIR) && npm run dev

mylife-build:
	cd $(MYLIFE_DIR) && npm run build

mylife-check:
	cd $(MYLIFE_DIR) && npx tsc --noEmit

mylife-test:
	cd $(MYLIFE_DIR) && npm test
