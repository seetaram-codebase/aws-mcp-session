const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("chatApi", {
  sendMessage: (text) => ipcRenderer.invoke("chat:send", text),
  confirmToolCall: (toolUseId, approved) => ipcRenderer.invoke("chat:confirm", { toolUseId, approved }),
});
