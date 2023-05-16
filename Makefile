# Catalina | 3.7
# Big Sur  | 3.8
# Ventura  | 3.9
SYSTEM_PYTHON                  := /usr/bin/python3
BOOTSTRAP_PYTHON_MAJOR_VERSION := $(shell ./semver.sh "$$($(SYSTEM_PYTHON) --version | cut -d' ' -f2)" | sed -n '1 p')
BOOTSTRAP_PYTHON_MINOR_VERSION := $(shell ./semver.sh "$$($(SYSTEM_PYTHON) --version | cut -d' ' -f2)" | sed -n '2 p')
BOOTSTRAP_PYTHON_VERSION       := $(BOOTSTRAP_PYTHON_MAJOR_VERSION).$(BOOTSTRAP_PYTHON_MINOR_VERSION)
BOOTSTRAP_PYTHON               := /usr/bin/python$(BOOTSTRAP_PYTHON_MAJOR_VERSION)
BOOTSTRAP_PYTHON_BIN_PATH      := ~/Library/Python/$(BOOTSTRAP_PYTHON_VERSION)/bin
BOOTSTRAP_PIP                  := $(BOOTSTRAP_PYTHON) -m pip

# NOTE: this reflects the "python3_version" in the "python" role in main.yml.
PYTHON_VERSION               := 3.11
PYTHON_BIN_PATH              := ~/Library/Python/$(PYTHON_VERSION)/bin
PYTHON                       := /usr/local/bin/python$(PYTHON_VERSION)
PIP                          := $(PYTHON) -m pip

PROJECT_ROOT                 := $(shell pwd)
ANSIBLE_DIRECTORY            := .
ANSIBLE_PLAYBOOKS_DIRECTORY  := $(ANSIBLE_DIRECTORY)
ANSIBLE_ROLES_DIRECTORY      := $(ANSIBLE_DIRECTORY)/roles
ANSIBLE_GROUP_VARS_DIRECTORY := $(ANSIBLE_DIRECTORY)/group_vars
ANSIBLE_INVENTORY            := $(ANSIBLE_DIRECTORY)/hosts
ANSIBLE_VERBOSE              := -v

ANSIBLE_GALAXY := $(PYTHON_BIN_PATH)/ansible-galaxy
ANSIBLE_LOCAL  := \
	$(PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "project_root=$(PROJECT_ROOT)" \
		--extra-vars "@$(ANSIBLE_GROUP_VARS_DIRECTORY)/localhost/vars.yml" \
		$(ARGS)

# NOTE: only required for Mojave?
.PHONY: get_bootstrap_pip
get_bootstrap_pip:
	curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
	python get-pip.py --user --force-reinstall
	rm -f get-pip.py

.PHONY: bootstrap
bootstrap:
	$(BOOTSTRAP_PIP) install --user ansible
	$(BOOTSTRAP_PYTHON_BIN_PATH)/ansible-playbook \
		--extra-vars "project_root=$(PROJECT_ROOT)" \
		$(ANSIBLE_PLAYBOOKS_DIRECTORY)/bootstrap.yml \
		--ask-become-pass
	$(PIP) install --user ansible
	$(ANSIBLE_GALAXY) collection install \
		-r $(ANSIBLE_DIRECTORY)/requirements.yml \
		-p $(ANSIBLE_DIRECTORY)/galaxy
	sudo softwareupdate --install-rosetta

# NOTE: this seems to be required for Big Sur. Without it, the 'cryptography'
# pip package (a transitive dependency for the 'ansible' package) fails to
# install.
.PHONY: bootstrap_upgrade_pip
bootstrap_upgrade_pip:
	@sudo $(BOOTSTRAP_PIP) install --upgrade pip

.PHONY: upgrade_pip
upgrade_pip:
	@$(PIP) install --upgrade pip

.PHONY: upgrade_ansible
upgrade_ansible:
	@$(PIP) install --user --upgrade ansible

.PHONY: converge
converge:
	@$(ANSIBLE_LOCAL) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/main.yml
