import adapter from "svelte-adapter-bun";

export default { kit: { adapter: adapter(), alias: { $view: "./src/view" } } };
