"""Stealth JavaScript patches (sd.txt "Stealth Implementation Architecture").

Injected as an init script so they run before any page script on every
navigation: navigator overrides, chrome runtime stub, canvas/WebGL noise,
WebRTC hardening, permission prompts auto-denied.
"""
from __future__ import annotations

STEALTH_INIT_SCRIPT = r"""
(() => {
  if (window.__uws_stealth__) return;
  window.__uws_stealth__ = true;

  const fp = window.__UWS_FP__ || {};

  // --- navigator.webdriver ------------------------------------------------
  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch (e) {}

  // --- languages / plugins -------------------------------------------------
  try {
    Object.defineProperty(navigator, 'languages', {get: () => fp.languages || ['en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => {
      const arr = [
        {name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
        {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
        {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
        {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
        {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: ''},
      ];
      arr.refresh = () => {};
      return arr;
    }});
  } catch (e) {}

  // --- platform ------------------------------------------------------------
  if (fp.platform) {
    try { Object.defineProperty(navigator, 'platform', {get: () => fp.platform}); } catch (e) {}
  }

  // --- window.chrome --------------------------------------------------------
  window.chrome = window.chrome || {runtime: {}, app: {isInstalled: false}};

  // --- Permissions.query ----------------------------------------------------
  try {
    const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
    window.navigator.permissions.query = (params) =>
      params && params.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : origQuery(params);
  } catch (e) {}

  // --- canvas noise ----------------------------------------------------------
  try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {
      const ctx = this.getContext('2d');
      if (ctx && this.width && this.height) {
        const amp = fp.canvasNoise || 0.02;
        try {
          const img = ctx.getImageData(0, 0, Math.min(this.width, 40), Math.min(this.height, 10));
          for (let i = 0; i < img.data.length; i += 4) {
            img.data[i] = Math.min(255, img.data[i] + (Math.random() * 2 - 1) * 255 * amp);
          }
          ctx.putImageData(img, 0, 0);
        } catch (e) {}
      }
      return origToDataURL.apply(this, args);
    };
  } catch (e) {}

  // --- WebGL vendor/renderer spoof -------------------------------------------
  try {
    const webglParams = [
      [WebGLRenderingContext.UNMASKED_VENDOR_WEBGL, fp.webglVendor || 'Intel Inc.'],
      [WebGLRenderingContext.UNMASKED_RENDERER_WEBGL, fp.webglRenderer || 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ];
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
      for (const [k, v] of webglParams) if (param === k) return v;
      return getParameter.apply(this, [param]);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
      const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function (param) {
        for (const [k, v] of webglParams) if (param === k) return v;
        return getParameter2.apply(this, [param]);
      };
    }
  } catch (e) {}

  // --- hardware hints ---------------------------------------------------------
  if (fp.deviceMemory) {
    try { Object.defineProperty(navigator, 'deviceMemory', {get: () => fp.deviceMemory}); } catch (e) {}
  }
  if (fp.cpuCores) {
    try { Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => fp.cpuCores}); } catch (e) {}
  }

  // --- notifications / dialogs / prompts ---------------------------------------
  try {
    Notification.requestPermission = (cb) => {
      if (cb) cb('denied');
      return Promise.resolve('denied');
    };
  } catch (e) {}
  try {
    const geo = navigator.geolocation;
    if (geo) {
      geo.getCurrentPosition = (succ, err) => err && err({code: 1, message: 'User denied Geolocation'});
      geo.watchPosition = (succ, err) => { err && err({code: 1, message: 'User denied Geolocation'}); return 0; };
    }
  } catch (e) {}

  // neutralize blocking JS dialogs (layer 5 of the popup handler)
  window.alert = () => {};
  window.confirm = () => true;
  window.prompt = () => null;

  // --- WebRTC leak hardening ---------------------------------------------------
  try {
    const rtc = window.RTCPeerConnection;
    if (rtc) {
      const OrigRTC = rtc.bind(window);
      window.RTCPeerConnection = function (config, ...rest) {
        if (config && config.iceServers) {
          config.iceServers = config.iceServers.filter(
            (s) => !(s.urls || '').startsWith('stun:')
          );
        }
        return new OrigRTC(config, ...rest);
      };
      window.RTCPeerConnection.prototype = OrigRTC.prototype;
    }
  } catch (e) {}
})();
"""


def stealth_init_script(fingerprint: dict) -> str:
    """Compose the init script with a baked-in fingerprint payload."""
    import json as _json

    payload = _json.dumps({
        "platform": fingerprint.get("platform"),
        "languages": fingerprint.get("locales", ["en-US", "en"]),
        "webglVendor": fingerprint.get("webgl_vendor"),
        "webglRenderer": fingerprint.get("webgl_renderer"),
        "deviceMemory": fingerprint.get("device_memory_gb"),
        "cpuCores": fingerprint.get("cpu_cores"),
        "canvasNoise": fingerprint.get("canvas_noise", 0.02),
    })
    return f"window.__UWS_FP__ = {payload};\n" + STEALTH_INIT_SCRIPT
