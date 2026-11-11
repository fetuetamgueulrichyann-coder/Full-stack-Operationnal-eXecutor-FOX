import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";

const authed = {
  "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }),
  "GET /settings/timezone": () => ({ body: { timezone: "Europe/Paris", now: "2026-11-10T10:00:00+01:00", zones: [] } }),
};

function open() {
  return render(
    <MemoryRouter initialEntries={["/reglages"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

/** Faux navigateur compatible avec les notifications. */
function fakePush(initial: { endpoint: string } | null = null, permission: NotificationPermission = "granted") {
  let sub: { endpoint: string; toJSON: () => unknown; unsubscribe: () => Promise<boolean> } | null = initial
    ? { endpoint: initial.endpoint, toJSON: () => ({ endpoint: initial.endpoint, keys: { p256dh: "P", auth: "A" } }), unsubscribe: async () => ((sub = null), true) }
    : null;
  const subscribe = vi.fn(async () => {
    sub = { endpoint: "https://fcm.googleapis.com/fcm/send/xyz", toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/xyz", keys: { p256dh: "P", auth: "A" } }), unsubscribe: async () => ((sub = null), true) };
    return sub;
  });
  const reg = { pushManager: { getSubscription: async () => sub, subscribe } };
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { getRegistration: async () => reg } });
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("Notification", Object.assign(class {}, { permission, requestPermission: vi.fn(async () => permission) }));
  return { subscribe };
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
  vi.unstubAllGlobals();
});

describe("Notifications (réglages)", () => {
  it("explique quand le navigateur ne sait pas", async () => {
    mockApi({ ...authed, "GET /push/config": () => ({ body: { enabled: true, public_key: "K", devices: 0 } }) });
    open();
    expect(await screen.findByText(/ne gère pas les notifications/)).toBeInTheDocument();
  });

  it("indique que le serveur n'est pas configuré", async () => {
    fakePush();
    mockApi({ ...authed, "GET /push/config": () => ({ body: { enabled: false, public_key: null, devices: 0 } }) });
    open();
    expect(await screen.findByText(/pas encore configurées sur ce serveur/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activer sur cet appareil" })).not.toBeInTheDocument();
  });

  it("active les notifications et envoie l'abonnement au serveur", async () => {
    const { subscribe } = fakePush();
    const { calls } = mockApi({
      ...authed,
      "GET /push/config": () => ({ body: { enabled: true, public_key: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", devices: 0 } }),
      "POST /push/subscribe": () => ({ status: 201, body: { status: "subscribed", devices: 1 } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Activer sur cet appareil" }));
    await waitFor(() => expect(subscribe).toHaveBeenCalled());
    await waitFor(() => expect(calls.find((c) => c.path === "/push/subscribe")?.body).toEqual({ endpoint: "https://fcm.googleapis.com/fcm/send/xyz", keys: { p256dh: "P", auth: "A" } }));
    expect(await screen.findByText("Notifications activées sur cet appareil.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Envoyer un test" })).toBeInTheDocument();
  });

  it("désactive et prévient le serveur", async () => {
    fakePush({ endpoint: "https://fcm.googleapis.com/fcm/send/abc" });
    const { calls } = mockApi({
      ...authed,
      "GET /push/config": () => ({ body: { enabled: true, public_key: "K", devices: 1 } }),
      "POST /push/unsubscribe": () => ({ body: { status: "unsubscribed", devices: 0 } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Désactiver sur cet appareil" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/push/unsubscribe")?.body).toEqual({ endpoint: "https://fcm.googleapis.com/fcm/send/abc" }));
    expect(await screen.findByRole("button", { name: "Activer sur cet appareil" })).toBeInTheDocument();
  });

  it("envoie une notification de test", async () => {
    fakePush({ endpoint: "https://fcm.googleapis.com/fcm/send/abc" });
    const { calls } = mockApi({
      ...authed,
      "GET /push/config": () => ({ body: { enabled: true, public_key: "K", devices: 1 } }),
      "POST /push/test": () => ({ body: { sent: 1 } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Envoyer un test" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/push/test")).toBe(true));
    expect(await screen.findByText("Notification de test envoyée.")).toBeInTheDocument();
  });

  it("montre le refus du navigateur", async () => {
    fakePush(null, "denied");
    mockApi({ ...authed, "GET /push/config": () => ({ body: { enabled: true, public_key: "K", devices: 0 } }) });
    open();
    expect(await screen.findByText(/sont bloquées pour ce site/)).toBeInTheDocument();
  });
});
