import { ApiError, NetworkError } from "./client";

/** Message à afficher à l'utilisateur pour une erreur d'API (jamais de détail technique). */
export function describeError(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "FOX met trop de temps à répondre. Réessayez dans un instant.";
  }
  if (error instanceof NetworkError) return error.message;
  if (error instanceof ApiError) {
    switch (error.code) {
      case "quota_exceeded":
        return "Vous avez atteint votre limite du jour. Réessayez demain.";
      case "model_unavailable":
      case "capacity_reached":
      case "upstream_error":
        return "FOX est momentanément indisponible. Réessayez dans un instant.";
      case "origin_forbidden":
      case "origin_missing":
      case "csrf_invalid":
        return "Votre session n'est plus valide. Rechargez la page.";
      case "internal_error":
        return "Une erreur est survenue côté serveur. Réessayez.";
      default:
        return error.message;
    }
  }
  return "Une erreur inattendue est survenue.";
}
