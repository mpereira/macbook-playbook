# macbook-playbook

Automatically prepare a Macbook for development and desktop tasks using Ansible.

## Last tested on

- MacBookPro14,3 with macOS 10.12.6

## Encrypted files (using Ansible Vault)

- Private SSH key: `ssh-keys/files/id_rsa`
- s3cmd configuraton: `s3cmd/files/.s3cfg`
- Enviroment variables for dotfiles:`dotfiles/vars/environment.yml`
- Prey API key: `prey/vars/api_key.yml`

## Configuring machine

### Manual steps

1. [Enable assistive access](http://mizage.com/help/accessibility.html)
2. System preferences -> Keyboard -> Modifier Keys -> "Caps Lock -> Control"
TODO
3. Set up Spotify Last.fm connect

### Clone repository

    $ git clone https://github.com/mpereira/macbook-playbook.git

### Prepare machine to run the Ansible playbooks (installs command line developer tools and Ansible)

    $ make bootstrap

### Create an Ansible Vault password

    $ echo 'SomePassword123$' > .ansible_vault_password

### Configure machine

    $ make converge

## Installs

Add:
- go
- dcos
- markdown
- shellcheck
- pyre-check
- cmake
- xz
- warsaw (módulo de segurança BB https://seg.bb.com.br/home.html)
- rescuetime
- yapf
- ripgrep
- terraform
- hugo
- black
- rust
- wrk
- bash
- wn
- pandoc
- consolas

TODO:
- ansible's pip aren't working (pip uses python2.7 under the hood, can't seem to
find packages on pypy). the black role for example uses `python 3 -m pip install
...` instead.

- pip install with `--prefix /usr/local` instead of `--user`.

- black can't be installed with `--prefix /usr/local`?
```
Traceback (most recent call last):
  File "/usr/local/bin/black", line 7, in <module>
    from black import main
ModuleNotFoundError: No module named 'black'
```

### Desktop Applications

- [Android File Transfer](https://www.android.com/filetransfer/)
- [Dash](https://kapeli.com/dash)
- [Dropbox](https://www.dropbox.com/install)
- [Firefox](https://www.mozilla.org/en-US/firefox/new)
- [Google Chrome](https://www.google.com/chrome/index.html)
- [Google Photos](https://photos.google.com/apps)
- [LICEcap](https://www.cockos.com/licecap/)
- [Skitch](https://evernote.com/products/skitch)
- [Steam](http://store.steampowered.com/about/)
- [Teensy Loader](https://www.pjrc.com/teensy/loader_mac.html)
- [Unity](https://unity3d.com/get-unity/download)
- [XQuartz](https://www.xquartz.org/)
- [YNAB](https://classic.youneedabudget.com/)

### Text Editors

- [Emacs 25](https://emacsformacosx.com/)
- [LightTable](http://lighttable.com/)
- [MacVim](http://macvim-dev.github.io/macvim/)
- [Neovim 1.5](https://github.com/neovim/neovim/wiki/Installing-Neovim)
- [Vim 8.0](http://www.vim.org/)

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
- [gnuplot](http://www.gnuplot.info/)
- [mpc](https://www.musicpd.org/clients/mpc/)
- [mpd](https://www.musicpd.org/download.html)
- [mpdscribble](https://www.musicpd.org/clients/mpdscribble/)
- [mpg123](https://www.mpg123.de/)
- [mplayer](https://mplayerosx.ch/)
- [shpotify](https://github.com/hnarayanan/shpotify)
- [Spotify](https://www.spotify.com/br/download/other/)
- [TagLib](http://taglib.org/)
- [Theremin](https://github.com/TheStalwart/Theremin)
- [vimpc](https://github.com/boysetsfrog/vimpc)
- [VLC](https://www.videolan.org/vlc/download-macosx.html)

### Browser Plugins

- [Firefox Adblock Plus](https://addons.mozilla.org/en-US/firefox/addon/adblock-plus/)
- [Firefox Pentadactyl](http://5digits.org/pentadactyl/)
- [Google Chrome Hangouts](https://tools.google.com/dlpage/hangoutplugin)
- [JRE](http://www.oracle.com/technetwork/java/javase/downloads/jre8-downloads-2133155.html)
- [Silverlight](https://www.microsoft.com/getsilverlight/Get-Started/Install/Default)

### Programming Languages

- [GNU Octave](https://www.gnu.org/software/octave/download.html)
- [Haskell Platform](https://www.haskell.org/platform/mac.html)
- [Java (JDK)](http://www.oracle.com/technetwork/pt/java/javase/downloads/jdk8-downloads-2133151.html)
- [Lua](https://www.lua.org/download.html)
- [LuaJIT](http://luajit.org/download.html)
- [Node.js](https://nodejs.org/en/download/)
- [PureScript](http://www.purescript.org/)
- [Python 3](https://www.python.org/downloads/mac-osx/)
- [R](https://cran.r-project.org/bin/macosx/)
- [Ruby](https://www.ruby-lang.org)

### Virtualization

- [Docker](https://store.docker.com/editions/community/docker-ce-desktop-mac)
- [Vagrant (with vagrant-vbguest plugin)](https://www.vagrantup.com/downloads.html)
- [Vagrant vagrant-vbguest plugin](https://github.com/dotless-de/vagrant-vbguest)
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads)

### Shell

- [fish-foreign-env](https://github.com/oh-my-fish/plugin-foreign-env)
- [fish](https://fishshell.com/)
- [iTerm](https://www.iterm2.com/)
- [oh-my-zsh](http://ohmyz.sh/)
- [Powerline](https://github.com/powerline/powerline)
- [tmux](https://github.com/tmux/tmux)
- [Zsh](http://www.zsh.org/)

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
- [chefdk](https://downloads.chef.io/chefdk)
- [Glances](https://nicolargo.github.io/glances/)
- [htop](http://hisham.hm/htop/)
- [vtop](https://github.com/MrRio/vtop)

### Document Processors

- [MacTeX](http://www.tug.org/mactex/)
- [OpenOffice](https://www.openoffice.org/porting/mac/)

### Package Managers and Build Tools

- [bundler](http://bundler.io/)
- [CMake](https://cmake.org/)
- [Homebrew](https://brew.sh/)
- [leiningen](https://leiningen.org/)
- [MacPorts](https://www.macports.org/)
- [pulp](https://www.npmjs.com/package/pulp)

### Miscellaneous

- [Ctags](https://ctags.io/)
- [defaultbrowser](https://github.com/kerma/defaultbrowser)
- [FontForge](http://fontforge.github.io/en-US/downloads/mac-dl/)
- [git](https://git-scm.com/download/mac)
- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)
- [Hugo](https://gohugo.io/)
- [jq](https://stedolan.github.io/jq/)
- [jsxhint](https://www.npmjs.com/package/jsxhint)
- [p7zip](http://www.7-zip.org/)
- [Qt 5](http://download.qt.io/official_releases/qt/5.9/5.9.2/)
- [reattach-to-user-namespace](https://github.com/ChrisJohnsen/tmux-MacOSX-pasteboard)
- [s3cmd](http://s3tools.org/s3cmd)
- [terminal-notifier](https://github.com/julienXX/terminal-notifier)
- [the-silver-searcher](https://geoff.greer.fm/ag/)
- [tmuxinator](https://github.com/tmuxinator/tmuxinator)
- [tree](http://brewformulas.org/Tree)
- [wget](https://www.gnu.org/software/wget/)
- [wkhtmltopdf](https://wkhtmltopdf.org/)

## Configures

- Passwordless sudo
- Remaps Caps-Lock to Control (currently broken on macOS Sierra)
- Puts SSH keys in place
- Patches Monaco font for Powerline

## Author

[Murilo Pereira](http://murilopereira.com)

## License

[MIT](http://opensource.org/licenses/MIT)
