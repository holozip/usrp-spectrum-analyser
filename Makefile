.PHONY: firmware-build firmware-diagnose firmware-install

firmware-build:
	./scripts/build-firmware.sh build

firmware-diagnose:
	./scripts/build-firmware.sh diagnose

firmware-install:
	./scripts/build-firmware.sh install
