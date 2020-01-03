BOOTSTRAP_PIP                   := pip
PIP                             := pip3
PYTHON2_VERSION                 := 2.7
PYTHON3_VERSION                 := 3.7
PYTHON2_BIN_PATH                := ~/Library/Python/$(PYTHON2_VERSION)/bin
PYTHON3_BIN_PATH                := ~/Library/Python/$(PYTHON3_VERSION)/bin
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
  $(ANSIBLE_ROLES_DIRECTORY)/awscli/files/credentials \
  $(ANSIBLE_ROLES_DIRECTORY)/ssh-keys/files/id_rsa \
  $(ANSIBLE_ROLES_DIRECTORY)/s3cmd/files/.s3cfg \
  $(ANSIBLE_ROLES_DIRECTORY)/dotfiles/vars/environment.yml \
  $(ANSIBLE_ROLES_DIRECTORY)/prey/vars/api_key.yml

ANSIBLE_COMMAND := \
	ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ARGS)

ANSIBLE_COMMAND_LOCAL := \
	ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)" \
		$(ARGS)

ANSIBLE_COMMAND_LOCAL_WITH_VAULT := \
	$(ANSIBLE_COMMAND_LOCAL) --vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

VAULT_COMMAND := \
	ansible-vault --vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

.PHONY: \
	clean_bootstrap_pip \
	get_bootstrap_pip \
	bootstrap \
	upgrade_pip \
	upgrade_ansible \
	converge \
	encrypt \
	decrypt \
	encrypt_pre_commit

# This might be required if you're seeing errors like "ImportError: No module
# named _internal".
# $ sudo python -m pip uninstall pip
# Uninstalling pip-9.0.1:
#   /Library/Python/2.7/site-packages/pip-9.0.1-py2.7.egg
#   /usr/local/bin/pip
#   /usr/local/bin/pip2
#   /usr/local/bin/pip2.7
clean_bootstrap_pip:
	sudo python -m pip uninstall pip

get_bootstrap_pip:
	curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
	python get-pip.py --user --force-reinstall
	rm -f get-pip.py

.ansible_vault_password:
	@test -s $(ANSIBLE_VAULT_PASSWORD_FILE) \
		|| echo "ATTENTION: Please create '$(PWD)/$(ANSIBLE_VAULT_PASSWORD_FILE)' with this project's Ansible Vault password" && exit 1

# TODO: install Python 3 manually outside Ansible?
bootstrap: .ansible_vault_password
	@sudo installer -package $(MACOS_SDK_HEADERS_PKG) -target /
	@export PATH="$(PYTHON2_BIN_PATH):$$PATH"; $(BOOTSTRAP_PIP) install --user --ignore-installed six ansible
	@export PATH="$(PYTHON2_BIN_PATH):$$PATH"; $(ANSIBLE_COMMAND_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/bootstrap.yml
	@export PATH="$(PYTHON3_BIN_PATH):$$PATH"; $(PIP) install --user --ignore-installed six ansible

upgrade_pip:
	@$(PIP) install --upgrade pip

# https://github.com/pypa/pip/issues/3165#issuecomment-146666737
upgrade_ansible:
	@$(PIP) install --user --upgrade --ignore-installed six ansible

converge:
	@$(ANSIBLE_COMMAND_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/main.yml

encrypt:
	@ansible-vault encrypt \
		--vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE) \
		$(ANSIBLE_SENSITIVE_CONTENT_FILES)

decrypt:
	@ansible-vault decrypt \
		--vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE) \
		$(ANSIBLE_SENSITIVE_CONTENT_FILES)

encrypt_pre_commit: encrypt
	@git add $(ANSIBLE_SENSITIVE_CONTENT_FILES)
