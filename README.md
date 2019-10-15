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
- [Dash](https://kapeli.com/dash)
- [Dropbox](https://www.dropbox.com/install)
- [Firefox](https://www.mozilla.org/en-US/firefox/new)
- [Google Chrome](https://www.google.com/chrome/index.html)
- [Google Photos](https://photos.google.com/apps)
- [LICEcap](https://www.cockos.com/licecap/)
- [RescueTime](https://www.rescuetime.com/)
- [Skitch](https://evernote.com/products/skitch) (disabled by default since macOS Mojave's screenshotting tools are good enough)
- [Steam](http://store.steampowered.com/about/)
- [Teensy Loader](https://www.pjrc.com/teensy/loader_mac.html)
- [Unity](https://unity3d.com/get-unity/download)
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
- [Skype](https://www.skype.com/en/download-skype/skype-for-mac/)
- [Slack](https://slack.com/downloads/osx)

### Configuration

- [dotemacs](https://github.com/mpereira/.emacs.d)
- [dotfiles](https://github.com/mpereira/dotfiles)

### Multimedia

- [Beets](http://beets.io/)
- [Cantata](https://github.com/CDrummond/cantata)
- [FFmpeg](http://www.ffmpegmac.net/)
- [gifsicle](https://www.lcdf.org/gifsicle/)
- [gnuplot](http://www.gnuplot.info/)
- [ImageMagick](https://www.imagemagick.org/)
- [mpc](https://www.musicpd.org/clients/mpc/)
- [mpd](https://www.musicpd.org/download.html)
- [mpdscribble](https://www.musicpd.org/clients/mpdscribble/)
- [mpg123](https://www.mpg123.de/)
- [mplayer](https://mplayerosx.ch/)
- [shpotify](https://github.com/hnarayanan/shpotify)
- [Spotify](https://www.spotify.com/br/download/other/)
- [TagLib](http://taglib.org/)
- [Theremin](https://github.com/TheStalwart/Theremin) (disabled by default, using Cantata)
- [vimpc](https://github.com/boysetsfrog/vimpc)
- [VLC](https://www.videolan.org/vlc/download-macosx.html)

### Browser Plugins

- [Firefox Adblock Plus](https://addons.mozilla.org/en-US/firefox/addon/adblock-plus/)
- [Firefox Pentadactyl](http://5digits.org/pentadactyl/)
- [Google Chrome Hangouts](https://tools.google.com/dlpage/hangoutplugin)
- [Warsaw (bb.com.br security module)](https://seg.bb.com.br/home.html)

### Programming Languages

- [GNU Octave](https://www.gnu.org/software/octave/download.html)
- [Go](https://golang.org/)
- [Haskell Platform](https://www.haskell.org/platform/mac.html)
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
- [tmux](https://github.com/tmux/tmux)
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

### UI

- [BitBar](https://getbitbar.com/)
- [Divvy](http://mizage.com/divvy/)

### Data Systems

- [Apache Hadoop](http://hadoop.apache.org/)
- [PostgreSQL 9.4](https://postgresapp.com/)
- [pgsanity](https://pypi.python.org/pypi/pgsanity)

### Configuration, Monitoring and Debugging

- [Apache JMeter](http://jmeter.apache.org/)
- [chefdk](https://downloads.chef.io/chefdk) (disabled by default since I don't use it anymore)
- [Glances](https://nicolargo.github.io/glances/)
- [htop](http://hisham.hm/htop/)
- [vtop](https://github.com/MrRio/vtop)

### Document Processors

- [MacTeX](http://www.tug.org/mactex/)
- [OpenOffice](https://www.openoffice.org/porting/mac/)

### Markup Tools

- [Hugo](https://gohugo.io/)
- [Markdown](https://daringfireball.net/projects/markdown/)
- [Pandoc](https://pandoc.org/)
- [wkhtmltopdf](https://wkhtmltopdf.org/)

### Miscellaneous

- [AWS CLI](https://aws.amazon.com/cli/)
- [Consolas font](https://docs.microsoft.com/en-us/typography/font-list/consolas)
- [defaultbrowser](https://github.com/kerma/defaultbrowser)
- [FontForge](http://fontforge.github.io/en-US/downloads/mac-dl/)
- [git](https://git-scm.com/download/mac)
- [jq](https://stedolan.github.io/jq/)
- [p7zip](http://www.7-zip.org/)
- [Qt 5](http://download.qt.io/official_releases/qt/5.9/5.9.2/)
- [reattach-to-user-namespace](https://github.com/ChrisJohnsen/tmux-MacOSX-pasteboard)
- [ripgrep](https://github.com/BurntSushi/ripgrep)
- [s3cmd](http://s3tools.org/s3cmd) (disabled by default since I use the aws CLI now)
- [tealdeer](https://tldr.sh/)
- [terminal-notifier](https://github.com/julienXX/terminal-notifier)
- [the-silver-searcher](https://geoff.greer.fm/ag/) (disabled by default since I use ripgrep now)
- [tmuxinator](https://github.com/tmuxinator/tmuxinator)
- [tree](http://brewformulas.org/Tree)
- [wget](https://www.gnu.org/software/wget/)
- [WordNet](https://wordnet.princeton.edu/download)
- [wrk](https://github.com/wg/wrk)
- [xz](https://tukaani.org/xz/)

## Configures

- Passwordless sudo
- Remaps Caps-Lock to Control
- Puts SSH keys in place
- Makes Google Chrome the default browser

## Author

[Murilo Pereira](http://murilopereira.com)

## License

[MIT](http://opensource.org/licenses/MIT)
