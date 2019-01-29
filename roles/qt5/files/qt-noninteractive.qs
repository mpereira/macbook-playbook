function Controller() {
  installer.autoRejectMessageBoxes();
  installer.installationFinished.connect(function () {
    gui.clickButton(buttons.NextButton);
  })
}

Controller.prototype.WelcomePageCallback = function () {
  gui.clickButton(buttons.NextButton, 3000);
}

Controller.prototype.CredentialsPageCallback = function () {
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.IntroductionPageCallback = function () {
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.TargetDirectoryPageCallback = function ()
{
  gui.currentPageWidget().TargetDirectoryLineEdit.setText("/Applications/Qt5.9.0");
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.ComponentSelectionPageCallback = function () {
  var widget = gui.currentPageWidget();

  widget.deselectAll();

  widget.selectComponent("qt.qt5.5120.clang_64")
  // widget.selectComponent("qt.qt5.5120.doc")
  widget.selectComponent("qt.qt5.5120.examples")
  widget.selectComponent("qt.qt5.5120.qtcharts")
  widget.selectComponent("qt.qt5.5120.qtcharts.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtdatavis3d")
  widget.selectComponent("qt.qt5.5120.qtdatavis3d.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtnetworkauth")
  widget.selectComponent("qt.qt5.5120.qtnetworkauth.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtpurchasing")
  widget.selectComponent("qt.qt5.5120.qtpurchasing.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtremoteobjects")
  widget.selectComponent("qt.qt5.5120.qtremoteobjects.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtscript")
  widget.selectComponent("qt.qt5.5120.qtspeech")
  widget.selectComponent("qt.qt5.5120.qtspeech.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtvirtualkeyboard")
  widget.selectComponent("qt.qt5.5120.qtvirtualkeyboard.gcc_64")
  widget.selectComponent("qt.qt5.5120.qtwebengine")
  widget.selectComponent("qt.qt5.5120.qtwebengine.gcc_64")
  // widget.selectComponent("qt.qt5.5120.src")
  widget.selectComponent("qt.qt5.tools.qtcreator")

  gui.clickButton(buttons.NextButton);
}

Controller.prototype.LicenseAgreementPageCallback = function () {
  gui.currentPageWidget().AcceptLicenseRadioButton.setChecked(true);
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.StartMenuDirectoryPageCallback = function () {
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.ReadyForInstallationPageCallback = function () {
  gui.clickButton(buttons.NextButton);
}

Controller.prototype.FinishedPageCallback = function () {
  var checkBoxForm = gui.currentPageWidget().LaunchQtCreatorCheckBoxForm
  if (checkBoxForm && checkBoxForm.launchQtCreatorCheckBox) {
    checkBoxForm.launchQtCreatorCheckBox.checked = false;
  }
  gui.clickButton(buttons.FinishButton);
}
