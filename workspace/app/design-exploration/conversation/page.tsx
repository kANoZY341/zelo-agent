import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@/components/design-exploration/conversation/conversation.css";
import {
  ConversationPreview,
  type ConversationScene,
} from "@/components/design-exploration/conversation/ConversationPreview";

const scenes: ConversationScene[] = [
  "home",
  "conversation",
  "listening",
  "thinking",
  "speaking",
  "result",
  "approval",
  "history",
];

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ state?: string | string[] }>;
}) {
  const query = await searchParams;
  const state =
    typeof query.state === "string" &&
    scenes.includes(query.state as ConversationScene)
      ? (query.state as ConversationScene)
      : "home";
  return <ConversationPreview scene={state} />;
}
