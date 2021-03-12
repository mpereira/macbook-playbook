# NOTE: this is the system Python version. In macOS Catalina it's 3.7.
BOOTSTRAP_PYTHON_MAJOR_VERSION  := 3
BOOTSTRAP_PYTHON_MINOR_VERSION  := 7
BOOTSTRAP_PYTHON_VERSION        := $(BOOTSTRAP_PYTHON_MAJOR_VERSION).$(BOOTSTRAP_PYTHON_MINOR_VERSION)
BOOTSTRAP_PYTHON                := /usr/bin/python$(BOOTSTRAP_PYTHON_MAJOR_VERSION)
BOOTSTRAP_PYTHON_BIN_PATH       := ~/Library/Python/$(BOOTSTRAP_PYTHON_VERSION)/bin
BOOTSTRAP_PIP                   := $(BOOTSTRAP_PYTHON) -m pip

PYTHON_VERSION                  := 3.9
PYTHON_BIN_PATH                 := ~/Library/Python/$(PYTHON_VERSION)/bin
PYTHON                          := /usr/local/bin/python$(PYTHON_VERSION)
PIP                             := $(PYTHON) -m pip

MACOS_VERSION                   := 10.14
MACOS_SDK_HEADERS_PKG           := /Library/Developer/CommandLineTools/Packages/macOS_SDK_headers_for_macOS_$(MACOS_VERSION).pkg
LOCAL_PROJECT_DIRECTORY         := $(shell pwd)
ANSIBLE_DIRECTORY               := .
ANSIBLE_PLAYBOOKS_DIRECTORY     := $(ANSIBLE_DIRECTORY)
ANSIBLE_ROLES_DIRECTORY         := $(ANSIBLE_DIRECTORY)/roles
ANSIBLE_INVENTORY               := $(ANSIBLE_DIRECTORY)/hosts
ANSIBLE_VERBOSE                 := -v
ANSIBLE_VAULT_PASSWORD_FILE     := $(ANSIBLE_DIRECTORY)/.ansible_vault_password
ANSIBLE_SENSITIVE_CONTENT_FILES := \
  $(ANSIBLE_ROLES_DIRECTORY)/better-touch-tool/files/license.xml \
  $(ANSIBLE_ROLES_DIRECTORY)/awscli/files/credentials \
  $(ANSIBLE_ROLES_DIRECTORY)/ssh-keys/files/id_rsa \
  $(ANSIBLE_ROLES_DIRECTORY)/s3cmd/files/.s3cfg \
  $(ANSIBLE_ROLES_DIRECTORY)/dotfiles/vars/environment.yml \
  $(ANSIBLE_ROLES_DIRECTORY)/prey/vars/api_key.yml
  $(ANSIBLE_ROLES_DIRECTORY)/istat-menus/files/iStat\ Menus\ Settings.ismp \

ANSIBLE := \
	$(PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ARGS)

ANSIBLE_LOCAL := \
	$(PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)" \
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
bootstrap: upgrade_pip
	$(BOOTSTRAP_PIP) install --user ansible
	sudo $(BOOTSTRAP_PYTHON_BIN_PATH)/ansible-playbook $(ANSIBLE_PLAYBOOKS_DIRECTORY)/bootstrap.yml
	$(PIP) install --user ansible

.PHONY: upgrade_pip
upgrade_pip:
	@$(PIP) install --upgrade pip

.PHONY: upgrade_ansible
upgrade_ansible:
	@$(PIP) install --user --upgrade ansible

.PHONY: converge
converge:
	@$(ANSIBLE_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/main.yml

.PHONY: encrypt
encrypt:
	$(call ANSIBLE_VAULT,encrypt) $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: decrypt
decrypt:
	$(call ANSIBLE_VAULT,decrypt) $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: truncate-sensitive-files
truncate-sensitive-files:
	@truncate --size 0 $(ANSIBLE_SENSITIVE_CONTENT_FILES)

.PHONY: encrypt_pre_commit
encrypt_pre_commit: encrypt
	@git add $(ANSIBLE_SENSITIVE_CONTENT_FILES)
