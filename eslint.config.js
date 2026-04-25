export default [
  {
    files: ["prices/static/prices/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: "script",
      globals: {
        AbortController: "readonly",
        Array: "readonly",
        Blob: "readonly",
        URL: "readonly",
        console: "readonly",
        document: "readonly",
        fetch: "readonly",
        FormData: "readonly",
        HTMLFormElement: "readonly",
        HTMLInputElement: "readonly",
        navigator: "readonly",
        Promise: "readonly",
        sessionStorage: "readonly",
        setInterval: "readonly",
        setTimeout: "readonly",
        window: "readonly"
      }
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["warn", { "argsIgnorePattern": "^_" }]
    }
  }
];
