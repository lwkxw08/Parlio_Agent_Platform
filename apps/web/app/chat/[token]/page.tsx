import { fetchChatConfig } from "@/lib/api";
import Chat from "./chat";

export const dynamic = "force-dynamic";

export default async function ChatPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const cfg = await fetchChatConfig(token);
  if (!cfg || !cfg.enabled) {
    return <div className="chat-embed"><div className="chat-body"><p className="muted" style={{ padding: "1rem" }}>Chat is currently unavailable.</p></div></div>;
  }
  return <Chat token={token} cfg={cfg} />;
}
