import { redirect } from "next/navigation";

// Notes is where a session starts; the old marketing-style home added a click.
export default function Home() {
  redirect("/notes");
}
