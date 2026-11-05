import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, unauthenticated, USER } from "../test/mockApi";

const authed = { "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }) };

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const task = (over: object = {}) => ({
  id: 1, title: "Envoyer le devis", priority: "normale", status: "a_faire", overdue: false,
  due_at: "2026-11-05T23:59+01:00", event_id: null, ...over,
});

describe("Tâches", () => {
  it("liste les tâches avec échéance, retard et rendez-vous lié", async () => {
    mockApi({
      ...authed,
      "GET /tasks?status=open": () => ({
        body: {
          tasks: [
            task({ overdue: true, due_at: "2026-10-30T23:59+01:00", priority: "haute" }),
            task({ id: 2, title: "Préparer les slides", due_at: "2026-11-02T12:00+01:00", event_id: 9, event_title: "Réunion" }),
          ],
        },
      }),
    });
    open("/taches");
    expect(await screen.findByText("Envoyer le devis")).toBeInTheDocument();
    expect(screen.getByText("En retard · 30/10")).toBeInTheDocument();
    expect(screen.getByText("Priorité haute")).toBeInTheDocument();
    expect(screen.getByText("Pour le 02/11 à 12:00")).toBeInTheDocument();
    expect(screen.getByText("Rdv : Réunion")).toBeInTheDocument();
  });

  it("termine une tâche puis recharge la liste", async () => {
    let tasks = [task()];
    const { calls } = mockApi({
      ...authed,
      "GET /tasks?status=open": () => ({ body: { tasks } }),
      "PATCH /tasks/1": () => {
        tasks = [];
        return { body: { task: task({ status: "terminee" }) } };
      },
    });
    open("/taches");
    await userEvent.click(await screen.findByRole("button", { name: "Terminer : Envoyer le devis" }));
    await waitFor(() => expect(screen.getByText("Aucune tâche ici.")).toBeInTheDocument());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "terminee" });
  });

  it("ajoute une tâche par saisie rapide", async () => {
    let tasks: ReturnType<typeof task>[] = [];
    const { calls } = mockApi({
      ...authed,
      "GET /tasks?status=open": () => ({ body: { tasks } }),
      "POST /tasks": (call) => {
        tasks = [task({ title: String((call.body as { text: string }).text) })];
        return { status: 201, body: { task: tasks[0] } };
      },
    });
    open("/taches");
    await userEvent.type(await screen.findByLabelText("Nouvelle tâche"), "Appeler Paul | demain");
    await userEvent.click(screen.getByRole("button", { name: "Ajouter" }));
    expect(await screen.findByText("Appeler Paul | demain")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/tasks")?.body).toEqual({ text: "Appeler Paul | demain" });
  });

  it("affiche l'erreur de l'API sans casser la page", async () => {
    mockApi({ ...authed, "GET /tasks?status=open": () => ({ status: 500, body: { error: "x", code: "internal_error" } }) });
    open("/taches");
    expect(await screen.findByRole("alert")).toHaveTextContent("Une erreur est survenue côté serveur");
  });

  it("renvoie vers la connexion si la session a expiré", async () => {
    mockApi({ "GET /auth/me": unauthenticated });
    open("/taches");
    expect(await screen.findByRole("heading", { name: "Connexion" })).toBeInTheDocument();
  });
});

const ev = (over: object = {}) => ({
  id: 5, title: "Point d'équipe", location: "Salle 2", start: "2026-11-04T09:00+01:00", end: "2026-11-04T09:30+01:00", ...over,
});

describe("Agenda", () => {
  it("regroupe les rendez-vous par jour et marque aujourd'hui", async () => {
    mockApi({
      ...authed,
      "GET /events?range=semaine": () => ({
        body: { today: "2026-11-04", events: [ev({ repeat: "du lundi au vendredi" }), ev({ id: 6, title: "Appel Paul", location: null, start: "2026-11-05T16:30+01:00", end: "2026-11-05T17:30+01:00" })] },
      }),
    });
    open("/agenda");
    expect(await screen.findByText("mer 04/11")).toBeInTheDocument();
    expect(screen.getByText("jeu 05/11")).toBeInTheDocument();
    expect(screen.getByText("Aujourd'hui", { selector: ".chip" })).toBeInTheDocument();
    expect(screen.getByText("09:00-09:30 · du lundi au vendredi")).toBeInTheDocument();
  });

  it("crée un rendez-vous et signale un chevauchement", async () => {
    let events: object[] = [];
    const { calls } = mockApi({
      ...authed,
      "GET /events?range=semaine": () => ({ body: { today: "2026-11-04", events } }),
      "POST /events": () => {
        events = [ev()];
        return { status: 201, body: { event: ev(), conflicts: [ev({ id: 8, title: "Dentiste" })] } };
      },
    });
    open("/agenda");
    await userEvent.click(await screen.findByRole("button", { name: "Ajouter un rendez-vous" }));
    await userEvent.type(screen.getByLabelText("Titre"), "Point d'équipe");
    await userEvent.type(screen.getByLabelText("Début"), "2026-11-04T09:00");
    await userEvent.selectOptions(screen.getByLabelText("Répétition"), "jours ouvrés");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByRole("status")).toHaveTextContent("chevauche : Dentiste");
    const sent = calls.find((c) => c.method === "POST" && c.path === "/events")?.body as Record<string, string>;
    expect(sent).toMatchObject({ title: "Point d'équipe", start: "2026-11-04T09:00", repeat: "jours ouvrés" });
    expect(sent.duration).toBeUndefined();
  });

  it("supprime seulement une occurrence d'une série, ou la série entière", async () => {
    const { calls } = mockApi({
      ...authed,
      "GET /events?range=semaine": () => ({ body: { today: "2026-11-04", events: [ev({ repeat: "chaque jour", occurrence: "2026-11-04" })] } }),
      "DELETE /events/5": () => ({ body: { status: "occurrence_deleted" } }),
    });
    open("/agenda");
    await userEvent.click(await screen.findByRole("button", { name: /Point d'équipe/ }));
    const detail = screen.getByRole("region", { name: "Détail du rendez-vous" });
    await userEvent.click(within(detail).getByRole("button", { name: "Supprimer seulement le 04/11" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ occurrence: "2026-11-04" });
  });

  it("supprime un rendez-vous simple sans paramètre d'occurrence", async () => {
    const { calls } = mockApi({
      ...authed,
      "GET /events?range=semaine": () => ({ body: { today: "2026-11-04", events: [ev()] } }),
      "DELETE /events/5": () => ({ body: { status: "deleted" } }),
    });
    open("/agenda");
    await userEvent.click(await screen.findByRole("button", { name: /Point d'équipe/ }));
    await userEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({});
  });
});
