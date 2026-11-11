// Service worker de FOX : garde la coquille de l'application pour un démarrage rapide.
// Il ne touche JAMAIS à /api (données privées, cookies de session) : tout ce qui est personnel reste en direct.
const CACHE = "fox-shell-v2";
const SHELL = ["/", "/favicon.png", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (req.mode === "navigate") {
    // Réseau d'abord ; hors ligne, on ressert la coquille (l'application affichera alors ses erreurs réseau).
    event.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    // Fichiers versionnés par leur nom : cache d'abord.
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(req, copy));
            }
            return res;
          }),
      ),
    );
  }
});

// --- Notifications push (rappels application fermée) -------------------------------------------------
// Le contenu est chiffré de bout en bout ; on n'accepte ici que du texte court et une adresse interne à FOX.
const safeUrl = (url) => (typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "/");

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = typeof data.title === "string" && data.title ? data.title.slice(0, 100) : "FOX";
  const options = {
    body: typeof data.body === "string" ? data.body.slice(0, 200) : "",
    tag: typeof data.tag === "string" ? data.tag.slice(0, 60) : undefined,
    icon: "/icon-192.png",
    badge: "/favicon.png",
    data: { url: safeUrl(data.url) },
  };
  event.waitUntil(
    (async () => {
      // Si FOX est déjà affiché et visible, le bandeau de rappel de la page suffit (sauf pour le test).
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      if (data.tag !== "test" && windows.some((w) => w.visibilityState === "visible")) return;
      await self.registration.showNotification(title, options);
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = safeUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of windows) {
        if ("focus" in w) {
          await w.focus();
          if ("navigate" in w) await w.navigate(url);
          return;
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});
