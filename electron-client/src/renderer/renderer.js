const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("input");

function appendMessage(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function appendConfirmCard(name, input) {
  const card = document.createElement("div");
  card.className = "bubble assistant confirm-card";

  const title = document.createElement("p");
  title.className = "confirm-title";
  title.textContent = `Claude wants to run "${name}" -- review before it goes live:`;
  card.appendChild(title);

  const pre = document.createElement("pre");
  pre.className = "confirm-code";
  pre.textContent = JSON.stringify(input, null, 2);
  card.appendChild(pre);

  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  const approveBtn = document.createElement("button");
  approveBtn.textContent = "Approve & Deploy";
  approveBtn.className = "approve";
  const denyBtn = document.createElement("button");
  denyBtn.textContent = "Deny";
  denyBtn.className = "deny";
  actions.appendChild(approveBtn);
  actions.appendChild(denyBtn);
  card.appendChild(actions);

  messagesEl.appendChild(card);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return { card, approveBtn, denyBtn };
}

/** Renders an agent response ({type:"text"} or {type:"confirm"}), wiring up
 * approve/deny buttons recursively since resolving one confirmation can
 * itself return another confirmation or the final text. */
function renderReply(reply) {
  if (reply.type === "text") {
    appendMessage("assistant", reply.text);
    return;
  }

  const { card, approveBtn, denyBtn } = appendConfirmCard(reply.name, reply.input);
  const decide = async (approved) => {
    approveBtn.disabled = true;
    denyBtn.disabled = true;
    card.classList.add("confirm-resolved");
    const next = await window.chatApi.confirmToolCall(reply.toolUseId, approved);
    renderReply(next);
  };
  approveBtn.addEventListener("click", () => decide(true));
  denyBtn.addEventListener("click", () => decide(false));
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = "";
  appendMessage("user", text);
  const pending = appendMessage("assistant pending", "Thinking...");

  const reply = await window.chatApi.sendMessage(text);
  pending.remove();
  renderReply(reply);
});
