import {el} from './ui.js';

function apply() {
  const landscape = localStorage.getItem('ember-landscape') || 'dusk';
  const hour = new Date().getHours();
  document.documentElement.dataset.landscape = landscape === 'clock'
    ? (hour < 6 || hour >= 21 ? 'night' : hour < 16 ? 'morning' : 'dusk') : landscape;
  document.documentElement.dataset.coat = localStorage.getItem('ember-coat') || 'red';
}
apply();
window.addEventListener('storage', apply);
document.addEventListener('visibilitychange',apply);
setInterval(apply,60000);

export function mountAppearance(root) {
  const section = el('section','appearance settings-form');
  section.innerHTML = '<section><h2>Clearing appearance</h2><div class="config-fields"><label>Landscape<select name="landscape"><option value="dusk">Sunset</option><option value="morning">Daylight</option><option value="night">Moonlight</option><option value="clock">Follow local time</option></select></label><label>Fox coat<select name="coat"><option value="red">Red fox</option><option value="silver">Silver fox</option><option value="arctic">Arctic fox</option></select></label></div></section>';
  for (const select of section.querySelectorAll('select')) {
    const key='ember-'+select.name;
    select.value=localStorage.getItem(key) || select.options[0].value;
    select.onchange=()=>{localStorage.setItem(key,select.value); apply();};
  }
  root.append(section);
}
