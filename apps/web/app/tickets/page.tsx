import TicketBoard from "./board";
import { fetchTickets } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Tickets() {
  const tickets = (await fetchTickets()) ?? [];
  return (
    <>
      <h1>Tickets &amp; callbacks</h1>
      <TicketBoard initial={tickets} />
    </>
  );
}
