# macbook-playbook

Automatically prepare a Macbook for development and desktop tasks using Ansible.

## Last tested on

- MacBookPro14,3 with macOS 10.12.6

## Encrypted files (using Ansible Vault)

- Private SSH key: `ssh-keys/files/id_rsa`
- AWS CLI credentials: `awscli/files/credentials`
- s3cmd configuration: `s3cmd/files/.s3cfg`
- Enviroment variables for dotfiles:`dotfiles/vars/environment.yml`
- Prey API key: `prey/vars/api_key.yml`

## Configuring machine

### Install Developer Tools (xcode-select)

    Open the Terminal app and type `git` into the shell.

### Clone repository

    $ git clone https://github.com/mpereira/macbook-playbook.git

### Create an Ansible Vault password

    $ echo 'SomePassword123$' > .ansible_vault_password

### Prepare machine to run the Ansible playbooks (installs command line developer tools and Ansible)

    $ make bootstrap

### Configure machine

    $ make converge

### Manual steps post `make converge`

1. [Enable assistive access for Divvy](http://mizage.com/help/accessibility.html)
2. Enable assistive access for Terminal
3. Enable assistive access for Emacs
5. Set up Spotify Last.fm connect

## Installs

### Desktop Applications

- [Android File Transfer](https://www.android.com/filetransfer/)
- [BitBar](https://getbitbar.com/)
- [Cantata](https://github.com/CDrummond/cantata)
- [Cursorcerer](http://doomlaser.com/cursorcerer-hide-your-cursor-at-will/)
- [Dash](https://kapeli.com/dash)
- [Divvy](http://mizage.com/divvy/)
- [Dropbox](https://www.dropbox.com/install)
- [Firefox](https://www.mozilla.org/en-US/firefox/new)
- [Google Chrome](https://www.google.com/chrome/index.html)
- [Google Photos](https://photos.google.com/apps)
- [LICEcap](https://www.cockos.com/licecap/)
- [RescueTime](https://www.rescuetime.com/)
- [Skitch](https://evernote.com/products/skitch) (disabled by default since
  macOS Mojave's screenshotting tools are good enough)
- [Skype](https://www.skype.com/en/download-skype/skype-for-mac/)
- [Slack](https://slack.com/downloads/osx)
- [Spotify](https://www.spotify.com/br/download/other/)
- [Steam](http://store.steampowered.com/about/)
- [Teensy Loader](https://www.pjrc.com/teensy/loader_mac.html)
- [Theremin](https://github.com/TheStalwart/Theremin) (disabled by default,
  using Cantata)
- [Unity](https://unity3d.com/get-unity/download)
- [VLC](https://www.videolan.org/vlc/download-macosx.html)
- [XQuartz](https://www.xquartz.org/)
- [YNAB](https://classic.youneedabudget.com/)

### Text Editors

- [Emacs 26](https://emacsformacosx.com/)
- [LightTable](http://lighttable.com/)
- [MacVim](http://macvim-dev.github.io/macvim/)
- [Neovim](https://github.com/neovim/neovim/wiki/Installing-Neovim)
- [Vim](http://www.vim.org/)

### Communication

- [irssi](https://irssi.org/)
- [IrssiNotifier](https://irssinotifier.appspot.com/)

### Configuration

- [dotemacs](https://github.com/mpereira/.emacs.d)
- [dotfiles](https://github.com/mpereira/dotfiles)

### Multimedia

- [Beets](http://beets.io/)
- [FFmpeg](http://www.ffmpegmac.net/)
- [gifsicle](https://www.lcdf.org/gifsicle/)
- [ImageMagick](https://www.imagemagick.org/)
- [mpc](https://www.musicpd.org/clients/mpc/)
- [mpd](https://www.musicpd.org/download.html)
- [mpdscribble](https://www.musicpd.org/clients/mpdscribble/)
- [mpg123](https://www.mpg123.de/)
- [mplayer](https://mplayerosx.ch/)
- [shpotify](https://github.com/hnarayanan/shpotify)
- [TagLib](http://taglib.org/)
- [vimpc](https://github.com/boysetsfrog/vimpc)

### Browser Plugins

- [Firefox Adblock Plus](https://addons.mozilla.org/en-US/firefox/addon/adblock-plus/)
- [Google Chrome Hangouts](https://tools.google.com/dlpage/hangoutplugin)
- [Warsaw (bb.com.br security module)](https://seg.bb.com.br/home.html)

### Programming Languages

- [GNU Octave](https://www.gnu.org/software/octave/download.html)
- [Go](https://golang.org/)
- [Haskell Platform](https://www.haskell.org/platform/mac.html) (disabled by
  default, using Haskell Stack)
- [Haskell Stack](https://docs.haskellstack.org/en/stable/README/)
- [Java (JDK)](http://www.oracle.com/technetwork/pt/java/javase/downloads/jdk8-downloads-2133151.html)
- [Lua](https://www.lua.org/download.html)
- [LuaJIT](http://luajit.org/download.html)
- [Node.js](https://nodejs.org/en/download/)
- [PureScript](http://www.purescript.org/)
- [Python 3](https://www.python.org/downloads/mac-osx/)
- [R](https://cran.r-project.org/bin/macosx/)
- [Ruby](https://www.ruby-lang.org)
- [Rust](https://www.rust-lang.org/)

### Virtualization, Provisioning, Containers and System Tools

- [chefdk](https://downloads.chef.io/chefdk) (disabled by default since I don't use it anymore)
- [Docker](https://store.docker.com/editions/community/docker-ce-desktop-mac)
- [kubectl](https://kubernetes.io/docs/reference/kubectl/kubectl/)
- [OpenZFS](https://openzfsonosx.org/)
- [Terraform](https://www.terraform.io/)
- [Vagrant vagrant-vbguest plugin](https://github.com/dotless-de/vagrant-vbguest)
- [Vagrant](https://www.vagrantup.com/downloads.html)
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads)

### Package Managers and Build Tools

- [bundler](http://bundler.io/)
- [CMake](https://cmake.org/)
- [GraalVM](https://www.graalvm.org/)
- [Homebrew](https://brew.sh/)
- [leiningen](https://leiningen.org/)
- [MacPorts](https://www.macports.org/)
- [Make](https://www.gnu.org/software/make/)
- [Maven](https://maven.apache.org/)
- [pulp](https://www.npmjs.com/package/pulp)

### Shell

- [Bash](https://www.gnu.org/software/bash/)
- [fish-foreign-env](https://github.com/oh-my-fish/plugin-foreign-env)
- [fish](https://fishshell.com/)
- [iTerm](https://www.iterm2.com/)
- [Powerline](https://github.com/powerline/powerline)
- [reattach-to-user-namespace](https://github.com/ChrisJohnsen/tmux-MacOSX-pasteboard)
  (for tmux)
- [tmux](https://github.com/tmux/tmux)
- [tmuxinator](https://github.com/tmuxinator/tmuxinator) (for tmux)
- [Zsh](http://www.zsh.org/)

### Programming Utilities

- [Black](https://black.readthedocs.io/en/stable/)
- [Ctags](https://ctags.io/)
- [gron](https://github.com/tomnomnom/gron)
- [jsxhint](https://www.npmjs.com/package/jsxhint)
- [ktlint](https://ktlint.github.io/)
- [node-cljfmt](https://github.com/snoe/node-cljfmt)
- [Prettier](https://prettier.io/)
- [Pyre](https://pyre-check.org/)
- [ShellCheck](https://www.shellcheck.net/)
- [shfmt](https://github.com/mvdan/sh)
- [YAPF](https://github.com/google/yapf)
- [yq](https://github.com/mikefarah/yq)

### Haskell Utilities

- [ghc-mod](https://hackage.haskell.org/package/ghc-mod)
- [hdevtools](https://hackage.haskell.org/package/hdevtools)
- [hindent](https://hackage.haskell.org/package/hindent)
- [hlint](https://hackage.haskell.org/package/hlint)
- [hoogle](https://hackage.haskell.org/package/hoogle)
- [pointfree](https://hackage.haskell.org/package/pointfree)

### Security

- [Prey](https://www.preyproject.com/download)
- [GnuPG](https://www.gnupg.org/download/index.html)
- [vault](https://github.com/jcoglan/vault)

### GNU Command Line Tools

- binutils
- coreutils
- diffutils
- ed
- findutils
- gawk
- gnu-indent
- gnu-sed
- gnu-tar
- gnu-which
- gnutls
- grep
- gzip
- screen
- watch
- wdiff
- wget

### Data Systems

- [Apache Hadoop](http://hadoop.apache.org/)
- [PostgreSQL 9.4](https://postgresapp.com/)

### Configuration, Monitoring and Debugging (tag: observability)

- [Apache JMeter](http://jmeter.apache.org/)
- [Glances](https://nicolargo.github.io/glances/)
- [htop](http://hisham.hm/htop/)
- [ngrep](https://github.com/jpr5/ngrep)
- [vtop](https://github.com/MrRio/vtop)

### Document Processors and Plotting

- [gnuplot](http://www.gnuplot.info/)
- [MacTeX](http://www.tug.org/mactex/)
- [OpenOffice](https://www.openoffice.org/porting/mac/)

### Markup Tools

- [Grip](https://github.com/joeyespo/grip)
- [Hugo](https://gohugo.io/)
- [Markdown](https://daringfireball.net/projects/markdown/)
- [Pandoc](https://pandoc.org/)
- [wkhtmltopdf](https://wkhtmltopdf.org/)

### Command line tools

- [AWS CLI](https://aws.amazon.com/cli/)
- [defaultbrowser](https://github.com/kerma/defaultbrowser)
- [git](https://git-scm.com/download/mac)
- [jq](https://stedolan.github.io/jq/)
- [p7zip](http://www.7-zip.org/)
- [pgsanity](https://pypi.python.org/pypi/pgsanity)
- [ripgrep](https://github.com/BurntSushi/ripgrep)
- [s3cmd](http://s3tools.org/s3cmd) (disabled by default since I use the aws CLI
  now)
- [tealdeer](https://tldr.sh/)
- [terminal-notifier](https://github.com/julienXX/terminal-notifier)
- [the-silver-searcher](https://geoff.greer.fm/ag/) (disabled by default since I
  use ripgrep now)
- [tree](http://brewformulas.org/Tree)
- [wrk](https://github.com/wg/wrk)
- [xz](https://tukaani.org/xz/)

### Miscellaneous

- [Consolas font](https://docs.microsoft.com/en-us/typography/font-list/consolas)
- [FontForge](http://fontforge.github.io/en-US/downloads/mac-dl/)
- [Qt 5](http://download.qt.io/official_releases/qt/5.9/5.9.2/)
- [WordNet](https://wordnet.princeton.edu/download)

## Configures

- Passwordless sudo
- Remaps Caps-Lock to Control
- Puts SSH keys in place
- Makes Google Chrome the default browser

## Author

[Murilo Pereira](http://murilopereira.com)

## License

[MIT](http://opensource.org/licenses/MIT)
