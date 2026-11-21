PREFIX ?= /usr/local

.PHONY: firmware-build firmware-diagnose firmware-install firmware-install-retune

firmware-build:
	./scripts/build-firmware.sh build

firmware-diagnose:
	./scripts/build-firmware.sh diagnose

firmware-install:
	./scripts/build-firmware.sh install

firmware-install-retune:
	cd resources/firmware && sha256sum --check SHA256SUMS
	install -D -m 0644 -- resources/firmware/usrp_b210_retune.bin "$(DESTDIR)$(PREFIX)/share/uhd/images/usrp_b210_retune.bin"
