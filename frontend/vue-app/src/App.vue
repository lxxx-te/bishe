<template>
  <div class="app">
    <nav class="nav">
      <router-link to="/events" class="brand">多源新闻聚合</router-link>
      <div class="nav-links">
        <router-link to="/events">事件流</router-link>
        <router-link to="/digest">每日简报</router-link>
        <router-link to="/chat">问答</router-link>
      </div>
    </nav>
    <router-view />
  </div>
</template>

<style>
.app {
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--sans);
}
.nav {
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 18px clamp(20px, 5vw, 48px);
  background: var(--surface);
  border-top: 3px solid var(--accent);
  border-bottom: 1px solid var(--line);
}
.brand {
  font-family: var(--serif);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink-strong);
  text-decoration: none;
  margin-right: auto;
}
.nav-links {
  display: flex;
  gap: 28px;
}
.nav a {
  color: var(--muted);
  text-decoration: none;
  font-size: 14px;
  letter-spacing: 0.02em;
  transition: color 0.15s;
}
.nav a:hover {
  color: var(--ink-strong);
}
.nav a.router-link-active {
  color: var(--ink-strong);
  font-weight: 600;
}
</style>