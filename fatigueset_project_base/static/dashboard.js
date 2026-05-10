(function(){
  const canvas = document.getElementById('chart');
  if (!canvas || !window.Chart) return;

  const ctx = canvas.getContext('2d');
  const sensor = (window.chartData && chartData.sensor) ? chartData.sensor : 'ACC';

  const colors = {
    'ACC': {line:'#e67e22', point:'#d35400', label:'Modulo Accelerazione'},
    'BVP': {line:'#3498db', point:'#2980b9', label:'BVP'},
    'EDA': {line:'#2ecc71', point:'#27ae60', label:'EDA'},
    'HR' : {line:'#e74c3c', point:'#c0392b', label:'Heart Rate'},
    'IBI': {line:'#9b59b6', point:'#8e44ad', label:'IBI'},
    'TEMP':{line:'#f1c40f', point:'#f39c12', label:'Temperatura'}
  };
  const cfg = colors[sensor] || {line:'#1a73e8', point:'#1a73e8', label:sensor};

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: (chartData && chartData.labels) ? chartData.labels : [],
      datasets: [{
        label: cfg.label,
        data: (chartData && chartData.values) ? chartData.values : [],
        borderColor: cfg.line,
        pointBackgroundColor: cfg.point
      }]
    },
    options: { responsive:true, maintainAspectRatio:false }
  });

  setTimeout(()=>window.location.reload(), 10000);
})();
