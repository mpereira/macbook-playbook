# Catalina | 3.7
# Big Sur  | 3.8
# Ventura  | 3.9
SYSTEM_PYTHON                   := /usr/bin/python3

BOOTSTRAP_PYTHON_MAJOR_VERSION  := $(shell ./semver.sh "$$($(SYSTEM_PYTHON) --version | cut -d' ' -f2)" | sed -n '1 p')
BOOTSTRAP_PYTHON_MINOR_VERSION  := $(shell ./semver.sh "$$($(SYSTEM_PYTHON) --version | cut -d' ' -f2)" | sed -n '2 p')
BOOTSTRAP_PYTHON_VERSION        := $(BOOTSTRAP_PYTHON_MAJOR_VERSION).$(BOOTSTRAP_PYTHON_MINOR_VERSION)
BOOTSTRAP_PYTHON                := /usr/bin/python$(BOOTSTRAP_PYTHON_MAJOR_VERSION)
BOOTSTRAP_PYTHON_BIN_PATH       := ~/Library/Python/$(BOOTSTRAP_PYTHON_VERSION)/bin
BOOTSTRAP_PIP                   := $(BOOTSTRAP_PYTHON) -m pip

# NOTE: this reflects the "python3_version" in the "python" role in main.yml.
PYTHON_VERSION                  := 3.10

PYTHON_BIN_PATH                 := ~/Library/Python/$(PYTHON_VERSION)/bin
PYTHON                          := /usr/local/bin/python$(PYTHON_VERSION)
PIP                             := $(PYTHON) -m pip

LOCAL_PROJECT_DIRECTORY         := $(shell pwd)
ANSIBLE_DIRECTORY               := .
ANSIBLE_PLAYBOOKS_DIRECTORY     := $(ANSIBLE_DIRECTORY)
ANSIBLE_ROLES_DIRECTORY         := $(ANSIBLE_DIRECTORY)/roles
ANSIBLE_INVENTORY               := $(ANSIBLE_DIRECTORY)/hosts
ANSIBLE_VERBOSE                 := -v
ANSIBLE_VAULT_PASSWORD_FILE     := $(ANSIBLE_DIRECTORY)/.ansible_vault_password
PROJECT_ROOT                    := .
ANSIBLE_SECRETS_DIRECTORY       := $(PROJECT_ROOT)/secrets
ANSIBLE_SENSITIVE_CONTENT_FILES := $(ANSIBLE_SECRETS_DIRECTORY)/*

ANSIBLE_GALAXY := $(PYTHON_BIN_PATH)/ansible-galaxy

ANSIBLE := \
	$(PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "project_root=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ARGS)

ANSIBLE_LOCAL := \
	$(PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "project_root=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ARGS)

ANSIBLE_LOCAL_WITH_VAULT := \
	$(ANSIBLE_LOCAL) --vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

ANSIBLE_VAULT = \
	$(PYTHON_BIN_PATH)/ansible-vault $(1) \
		--vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

# NOTE: only required for Mojave?
.PHONY: get_bootstrap_pip
get_bootstrap_pip:
	curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
	python get-pip.py --user --force-reinstall
	rm -f get-pip.py

.PHONY: .ansible_vault_password
.ansible_vault_password:
	@test -s $(ANSIBLE_VAULT_PASSWORD_FILE) \
		|| echo "ATTENTION: Please create '$(PWD)/$(ANSIBLE_VAULT_PASSWORD_FILE)' with this project's Ansible Vault password" && exit 1

.PHONY: bootstrap
bootstrap:
	$(BOOTSTRAP_PIP) install --user ansible
	$(BOOTSTRAP_PYTHON_BIN_PATH)/ansible-playbook \
		--extra-vars "project_root=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ANSIBLE_PLAYBOOKS_DIRECTORY)/bootstrap.yml
	$(PIP) install --user ansible
	$(ANSIBLE_GALAXY) collection install \
		-r $(ANSIBLE_DIRECTORY)/requirements.yml \
		-p $(ANSIBLE_DIRECTORY)/galaxy

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
	@$(ANSIBLE_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/main.yml

.PHONY: truncate-sensitive-files
truncate-sensitive-files:
	@truncate --size 0 $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: encrypt_all
encrypt_all:
	$(call ANSIBLE_VAULT,encrypt) $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: decrypt
decrypt_all:
	$(call ANSIBLE_VAULT,decrypt) $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: encrypt
encrypt:
	$(call ANSIBLE_VAULT,encrypt) $(FILES)

.PHONY: decrypt
decrypt:
	$(call ANSIBLE_VAULT,decrypt) $(FILES)

.PHONY: encrypt
view-encrypted:
	$(call ANSIBLE_VAULT,view) $(FILES)

.PHONY: encrypt_pre_commit
encrypt_pre_commit: encrypt
	@git add $(FILES)
