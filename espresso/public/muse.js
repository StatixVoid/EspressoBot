"use strict";
(() => {
  const page=document.querySelector(".espresso-artwork");
  if(!page || !("IntersectionObserver" in window))return;
  const hero=page.querySelector(".hero"),footer=page.querySelector(".site-footer");
  const reduced=matchMedia("(prefers-reduced-motion: reduce)"),wide=matchMedia("(min-width:1900px)");
  const sections=Array.from(page.querySelectorAll("main > section:not(.hero)"));
  let revealObserver=null,frame=0;
  const heroObserver=new IntersectionObserver(entries=>{
    page.classList.toggle("muse-docked",!entries[0].isIntersecting);
  });
  const footerObserver=new IntersectionObserver(entries=>page.classList.toggle("muse-near-footer",entries[0].isIntersecting));
  heroObserver.observe(hero);footerObserver.observe(footer);
  function setMotion(){
    revealObserver?.disconnect();
    page.classList.toggle("muse-motion",!reduced.matches);
    if(reduced.matches){sections.forEach(section=>section.classList.add("is-visible"));return;}
    revealObserver=new IntersectionObserver(entries=>{
      for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add("is-visible");revealObserver.unobserve(entry.target);}
    },{rootMargin:"0px 0px -20px 0px",threshold:0});
    for(const section of sections){section.classList.add("muse-reveal");revealObserver.observe(section);}
  }
  function drift(){
    if(frame || reduced.matches || !wide.matches || document.hidden || page.classList.contains("motion-paused"))return;
    frame=requestAnimationFrame(()=>{
      frame=0;
      const amount=Math.sin(scrollY/700)*14;
      page.style.setProperty("--muse-drift",amount.toFixed(1)+"px");
    });
  }
  addEventListener("scroll",drift,{passive:true});
  reduced.addEventListener("change",setMotion);wide.addEventListener("change",drift);
  addEventListener("pagehide",()=>{cancelAnimationFrame(frame);frame=0;});
  setMotion();
})();
