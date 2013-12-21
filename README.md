# macbook-playbook

Automatically prepare a Macbook for development and desktop tasks using Ansible.

## Tested on

- MacBookPro11,1 with OS X 10.9 (13A2093)

## Assumptions

- You have a clean installation of OS X
- You have a Dropbox account
- There is a directory named "ssh" under your Dropbox root directory.
- Your public and private SSH keys are inside the aforementioned "ssh"
  directory.
- There is a git repository for your dotfiles and it installs itself with `rake
  install`.
- There is a git repository for your Vim configuration files.

## Configuring machine

### Prepare machine to run the Ansible playbooks

    $ ./scripts/bootstrap

### Configure machine

    $ ansible-playbook main.yml -i hosts

## Installs

### Desktop applications

- Dropbox
- Firefox (with pentadactyl plugin)
- Google Chrome
- iTerm
- Skitch
- Skype
- XQuartz

### Plugins

- Hangouts
- JRE
- Silverlight

### Development programs

- Homebrew
- JDK
- oh-my-zsh
- Powerline
- R
- Vagrant (with vagrant-vbguest plugin)
- VirtualBox
- Zsh

#### Ruby gems

- bundler
- tmuxinator

#### Homebrew packages

- ctags
- fontforge
- htop
- irssi
- jshint
- mpc
- mpd
- mpdscribble
- node
- reattach-to-user-namespace
- the-silver-searcher
- tmux
- tree
- tree
- vault
- vimpc
- wget

### Git repositories

- dotfiles
- vimfiles

## Configures

- Remaps Caps-Lock to Control
- Puts SSH keys in place
- Patches Monaco font for Powerline

## TODO

- Add user in the sudoers list in `script/bootstrap`.
- Add notes regarding encrypting and storing SSH keys on Dropbox.
- Fix [Irssi notify.pl error](https://gist.github.com/mpereira/8071720).
- Integrate https://github.com/mathiasbynens/dotfiles/blob/master/.osx?
- Configure iTerm to use Monaco For Powerline.
- Move hard coded values to configurable variables.
- Make it more portable, so that it can be useful to more people.
- Abstract repeated installation tasks.
- Extract roles to a different project?
- Improve README with rationale, useful prose, etc.
- Twitter For Mac?
- GitHub For Mac?
- Transmission For Mac?

## Author

[Murilo Pereira](http://murilopereira.com)

## License

[MIT](http://opensource.org/licenses/MIT)