export default {
  defaultBrowser: "Google Chrome",
  rewrite: [
    {
      match: url =>
        ["http:", "https:"].includes(url.protocol) &&
        url.hostname === "app.clickup.com",
      url: url =>
        `clickup://${url.host}${url.pathname}${url.search}${url.hash}`,
    },
  ],
  handlers: [
    {
      match: url => url.protocol === "clickup:",
      browser: "ClickUp",
    },
  ],
};
