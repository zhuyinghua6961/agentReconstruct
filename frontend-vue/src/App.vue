<script setup>
// App.vue - 路由容器
</script>

<template>
  <router-view class="app-view" />
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body {
  height: 100%;
  overflow: hidden;
}

#app {
  height: 100%;
}

.app-view {
  height: 100%;
  overflow-y: auto;
}
</style>
