ObjC.import("AppKit");

["http", "https"].map(scheme => {
  const url = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL(
    $.NSURL.URLWithString(`${scheme}://example.com`)
  );
  return ObjC.unwrap($.NSBundle.bundleWithURL(url).bundleIdentifier);
}).join("\n");
