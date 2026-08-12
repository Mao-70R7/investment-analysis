function redactSensitiveText(text) {
  if (!text) return text;
  return text
    .replace(/\b1\d{10}\b/g, "[PHONE]")
    .replace(/\u6bdb\u5bb6\u8f69/g, "[NAME]")
    .replace(/[\u4e00-\u9fff]{2,4}\uff0c\u60a8\u597d/g, "[NAME], hello")
    .replace(/userId%3D[^%&]+/g, "userId%3D[USER_ID]")
    .replace(/loginname%3D[^%&]+/g, "loginname%3D[LOGIN]")
    .replace(/mobileNo%3D[^%&]+/g, "mobileNo%3D[PHONE]")
    .replace(/(logpassword|password|passwd|newCrpPwd|encryptPwd)=([^&\s]+)/gi, "$1=[SECRET]")
    .replace(/(logpassword|password|passwd|newCrpPwd|encryptPwd)%3D[^%&]+/gi, "$1%3D[SECRET]")
    .replace(/name%3D(?:%[0-9A-Fa-f]{2})+/g, "name%3D[NAME]")
    .replace(/hashCode%3D[^%&]+/g, "hashCode%3D[HASH]")
    .replace(/timestamp%3D\d+/g, "timestamp%3D[TIMESTAMP]")
    .replace(/SECURE_TOKEN=[A-Za-z0-9]+/g, "SECURE_TOKEN=[TOKEN]")
    .replace(/SUBMIT_TOKEN=[A-Za-z0-9]+/g, "SUBMIT_TOKEN=[TOKEN]");
}

function redactObject(value) {
  if (typeof value === "string") return redactSensitiveText(value);
  if (Array.isArray(value)) return value.map(redactObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, val]) => [key, redactObject(val)]));
  }
  return value;
}

module.exports = {
  redactObject,
  redactSensitiveText,
};
