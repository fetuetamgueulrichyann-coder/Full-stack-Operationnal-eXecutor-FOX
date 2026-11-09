import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Les attentes asynchrones (findBy…, waitFor) tolèrent une machine lente.
configure({ asyncUtilTimeout: 8000 });

afterEach(() => {
  cleanup();
});
