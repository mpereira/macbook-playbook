BOOTSTRAP_PIP                   := pip
PIP                             := pip3
LOCAL_PROJECT_DIRECTORY         := $(shell pwd)
ANSIBLE_DIRECTORY               := .
ANSIBLE_PLAYBOOKS_DIRECTORY     := $(ANSIBLE_DIRECTORY)
ANSIBLE_ROLES_DIRECTORY         := $(ANSIBLE_DIRECTORY)/roles
ANSIBLE_INVENTORY               := $(ANSIBLE_DIRECTORY)/hosts
ANSIBLE_VERBOSE                 := -vv
ANSIBLE_VAULT_PASSWORD_FILE     := $(ANSIBLE_DIRECTORY)/.ansible_vault_password
ANSIBLE_SENSITIVE_CONTENT_FILES := \
  $(ANSIBLE_ROLES_DIRECTORY)/ssh-keys/files/id_rsa \
  $(ANSIBLE_ROLES_DIRECTORY)/s3cmd/files/.s3cfg \
  $(ANSIBLE_ROLES_DIRECTORY)/dotfiles/vars/environment.yml \
  $(ANSIBLE_ROLES_DIRECTORY)/prey/vars/api_key.yml

ANSIBLE_COMMAND := \
	ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)"

ANSIBLE_COMMAND_LOCAL := \
	ansible-playbook $(ANSIBLE_VERBOSE) \
		-i $(ANSIBLE_INVENTORY) \
		--extra-vars "local_project_directory=$(LOCAL_PROJECT_DIRECTORY)"

ANSIBLE_COMMAND_LOCAL_WITH_VAULT := \
	$(ANSIBLE_COMMAND_LOCAL) --vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

VAULT_COMMAND := \
	ansible-vault --vault-password-file $(ANSIBLE_VAULT_PASSWORD_FILE)

.PHONY: \
	upgrade_pip \
	upgrade_ansible \
	bootstrap \
	converge \
	encrypt \
	decrypt \
	encrypt_pre_commit

upgrade_pip:
	@sudo $(PIP) install --upgrade pip

# https://github.com/pypa/pip/issues/3165#issuecomment-146666737
upgrade_ansible:
	@$(PIP) install --user --upgrade --ignore-installed six ansible

.ansible_vault_password:
	@test -s $(ANSIBLE_VAULT_PASSWORD_FILE) \
		|| echo "ATTENTION: Please create '$(PWD)/$(ANSIBLE_VAULT_PASSWORD_FILE)' with this project's Ansible Vault password" && exit 1

# TODO: install Python 3 manually outside Ansible?
bootstrap: .ansible_vault_password
	@sudo -H easy_install pip
	@$(BOOTSTRAP_PIP) install --user --ignore-installed six ansible
	@export PATH="~/Library/Python/2.7/bin:${PATH}"
	@$(ANSIBLE_COMMAND_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/bootstrap.yml

converge:
	@$(ANSIBLE_COMMAND_LOCAL_WITH_VAULT) $(ANSIBLE_PLAYBOOKS_DIRECTORY)/main.yml

encrypt:
	@$(VAULT_COMMAND) encrypt $(ANSIBLE_SENSITIVE_CONTENT_FILES)

decrypt:
	@$(VAULT_COMMAND) decrypt $(ANSIBLE_SENSITIVE_CONTENT_FILES)

encrypt_pre_commit: encrypt
	@git add $(ANSIBLE_SENSITIVE_CONTENT_FILES)
