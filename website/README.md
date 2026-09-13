# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

### Installation

```
$ npm ci
```

### Local Development

```
$ npm start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

### Build

```
$ npm run build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service. `onBrokenLinks` is set to `throw`, so a build also checks every internal link.

### Deployment

The site is published to GitHub Pages by the `deploy-doc.yml` workflow on every push to `main`; there is nothing to run by hand.
