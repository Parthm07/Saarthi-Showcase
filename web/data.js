/* Demo data — Aadhya Enterprises, Indore (FMCG). All figures are illustrative. */
const COMPANY = { name: 'Aadhya Enterprises', city: 'Indore', today: 'Friday, 11 September 2026' };

// Sold by the case. price = our rate per case, mrp = MRP per case, stock in cases,
// daily = avg cases sold per day, lead = supplier lead time in days
const PRODUCTS = [
  { id:'P1',  name:'Parle-G Biscuit 250g',          unit:'48 pcs/case',  price:1056, mrp:1200, stock:140, daily:9,   lead:5,  brand:'Parle',        scheme:{ min:10, label:'Buy 10 cases, get 1 free' } },
  { id:'P2',  name:'Good Day Cashew 200g',          unit:'36 pcs/case',  price:1250, mrp:1440, stock:60,  daily:3.5, lead:7,  brand:'Britannia' },
  { id:'P3',  name:"Lay's Classic Salted 52g",      unit:'60 pcs/case',  price:1020, mrp:1200, stock:90,  daily:6,   lead:5,  brand:'PepsiCo',      scheme:{ min:15, label:'5% off on 15+ cases' } },
  { id:'P4',  name:"Haldiram's Aloo Bhujia 400g",   unit:'20 pcs/case',  price:1900, mrp:2200, stock:45,  daily:2,   lead:7,  brand:"Haldiram's" },
  { id:'P5',  name:'Tata Tea Premium 1kg',          unit:'12 pcs/case',  price:5580, mrp:6240, stock:38,  daily:3,   lead:10, brand:'Tata Consumer' },
  { id:'P6',  name:'Nescafé Classic 50g',           unit:'48 pcs/case',  price:7780, mrp:8640, stock:12,  daily:1.6, lead:12, brand:'Nestlé' },
  { id:'P7',  name:'Frooti Mango 600ml',            unit:'24 pcs/case',  price:816,  mrp:960,  stock:200, daily:11,  lead:4,  brand:'Parle Agro',   scheme:{ min:20, label:'₹20/case off on 20+ cases' } },
  { id:'P8',  name:'Cadbury Dairy Milk 50g',        unit:'120 pcs/case', price:5220, mrp:6000, stock:20,  daily:1.4, lead:10, brand:'Mondelez' },
  { id:'P9',  name:'Colgate Strong Teeth 200g',     unit:'36 pcs/case',  price:3480, mrp:3960, stock:55,  daily:2.2, lead:7,  brand:'Colgate' },
  { id:'P10', name:'Lifebuoy Soap 125g ×4',         unit:'24 pcs/case',  price:2950, mrp:3360, stock:70,  daily:3,   lead:7,  brand:'HUL' },
  { id:'P11', name:'Clinic Plus Shampoo 340ml',     unit:'24 pcs/case',  price:4410, mrp:5040, stock:9,   daily:1.1, lead:12, brand:'HUL' },
  { id:'P12', name:'Parachute Coconut Oil 500ml',   unit:'24 pcs/case',  price:4860, mrp:5520, stock:64,  daily:.6,  lead:10, brand:'Marico',       slow:true },
  { id:'P13', name:'Surf Excel Easy Wash 1kg',      unit:'18 pcs/case',  price:2290, mrp:2610, stock:80,  daily:4,   lead:7,  brand:'HUL' },
  { id:'P14', name:'Vim Bar 300g',                  unit:'60 pcs/case',  price:1560, mrp:1800, stock:75,  daily:3,   lead:7,  brand:'HUL' },
  { id:'P15', name:'Harpic Power Plus 1L',          unit:'12 pcs/case',  price:2200, mrp:2520, stock:54,  daily:.6,  lead:7,  brand:'Reckitt',      slow:true },
  { id:'P16', name:'Aashirvaad Atta 5kg',           unit:'4 bags/bale',  price:1010, mrp:1120, stock:120, daily:8,   lead:5,  brand:'ITC' },
  { id:'P17', name:'Fortune Sunflower Oil 1L',      unit:'12 pcs/case',  price:1620, mrp:1800, stock:95,  daily:5,   lead:5,  brand:'Adani Wilmar' },
  { id:'P18', name:'Maggi Masala Noodles 280g',     unit:'48 pcs/case',  price:2350, mrp:2688, stock:36,  daily:2.4, lead:8,  brand:'Nestlé' },
];

const REPS = [
  { id:'s1', name:'Rahul Verma',  ini:'RV', territory:'Indore West & Central', target:1200000, achieved:784000, visitCov:71, conv:38, accept:46, aov:38400 },
  { id:'s2', name:'Priya Nair',   ini:'PN', territory:'Indore East',           target:1000000, achieved:812000, visitCov:88, conv:52, accept:61, aov:51200 },
  { id:'s3', name:'Amit Joshi',   ini:'AJ', territory:'Pithampur',             target:1400000, achieved:690000, visitCov:54, conv:29, accept:31, aov:44900 },
  { id:'s4', name:'Sanjay Patel', ini:'SP', territory:'Dewas – Ujjain',        target:900000,  achieved:603000, visitCov:63, conv:41, accept:44, aov:29800 },
];

/* buys: regular purchase patterns mined from invoices (qty in cases)
   cyc = normal reorder window (days), last = days since last bought, freq = [in n, of last m invoices], core = habitual line
   cross: suggestions — type cross | upsell | slow */
const CUSTOMERS = [
  { id:'c1', name:'Sharma Kirana Stores', area:'Plot 41, Sanwer Road', territory:'Indore West', rep:'s1', contact:'Rakesh Sharma', phone:'98260 41127',
    lastDays:8, lastVisit:8, aov:38500, outstanding:42300, limit:150000, overdue:0, trend:[41200, 38600, 44100, 36900, 39500, 14800],
    buys:[ { p:'P1', cyc:[7, 10], last:8, qty:10, freq:[6, 6] }, { p:'P5', cyc:[10, 14], last:8, qty:3, freq:[5, 6] }, { p:'P16', cyc:[7, 10], last:8, qty:15, freq:[6, 6], core:true }, { p:'P13', cyc:[10, 14], last:8, qty:6, freq:[4, 6] }, { p:'P7', cyc:[7, 10], last:8, qty:8, freq:[5, 6] } ],
    cross:[ { p:'P3', qty:5, type:'cross', why:'Sells with Parle-G at kiranas', peer:"7 of 9 kiranas that stock Parle-G also sell Lay's" } ] },
  { id:'c2', name:'Gupta General Stores', area:'22, Pologround Main Road', territory:'Indore West', rep:'s1', contact:'Manoj Gupta', phone:'93021 88540',
    lastDays:5, lastVisit:5, aov:52000, outstanding:96000, limit:125000, overdue:38400, overdueDays:47, trend:[58100, 61400, 55200, 60300, 49800, 21300],
    buys:[ { p:'P9', cyc:[10, 14], last:12, qty:4, freq:[5, 6] }, { p:'P10', cyc:[7, 10], last:5, qty:8, freq:[6, 6] }, { p:'P13', cyc:[7, 10], last:5, qty:10, freq:[6, 6], core:true }, { p:'P14', cyc:[10, 14], last:12, qty:6, freq:[5, 6] } ],
    cross:[ { p:'P11', qty:3, type:'cross', why:'Soap buyers add shampoo', peer:'82% of stores stocking Lifebuoy also stock Clinic Plus' } ] },
  { id:'c3', name:'Maa Durga Provision Store', area:'Sector E, Sanwer Road', territory:'Indore West', rep:'s1', contact:'Dinesh Chouhan', phone:'99077 31264',
    lastDays:10, lastVisit:10, aov:27500, outstanding:18200, limit:80000, overdue:0, trend:[31400, 29100, 33000, 24200, 14100, 6200], churn:52,
    buys:[ { p:'P1', cyc:[7, 10], last:10, qty:8, freq:[6, 6] }, { p:'P16', cyc:[7, 10], last:10, qty:10, freq:[6, 6] }, { p:'P17', cyc:[10, 14], last:10, qty:4, freq:[4, 6] } ],
    cross:[] },
  { id:'c4', name:'New Bharat Super Mart', area:'Palda Main Road', territory:'Indore West', rep:'s1', contact:'Harpreet Singh', phone:'98930 22618',
    lastDays:6, lastVisit:6, aov:64000, outstanding:71000, limit:200000, overdue:0, trend:[62300, 70100, 66400, 72800, 68200, 31000],
    buys:[ { p:'P5', cyc:[10, 14], last:6, qty:4, freq:[5, 6] }, { p:'P6', cyc:[14, 20], last:6, qty:2, freq:[6, 6] }, { p:'P18', cyc:[7, 10], last:6, qty:6, freq:[4, 6] } ],
    cross:[ { p:'P12', qty:4, type:'slow', why:'Supermarts this size sell coconut oil', peer:'They sell 4–6 cases a month and buy it elsewhere' } ] },
  { id:'c5', name:'ABC Traders', area:'Siyaganj Main Road', territory:'Indore East', rep:'s2', contact:'Anil Bansal', phone:'94250 67103',
    lastDays:4, lastVisit:3, aov:78000, outstanding:135000, limit:198000, overdue:52000, overdueDays:38, trend:[82400, 76200, 88100, 91300, 79600, 40200],
    buys:[ { p:'P16', cyc:[5, 7], last:4, qty:40, freq:[6, 6] }, { p:'P17', cyc:[5, 7], last:4, qty:30, freq:[6, 6], core:true }, { p:'P7', cyc:[7, 10], last:4, qty:25, freq:[4, 6] } ],
    cross:[ { p:'P8', qty:3, type:'upsell', why:'Festive chocolate, better margin', peer:'Sells 30 cases of toffees a month; Dairy Milk earns 2× the margin' } ] },
  { id:'c6', name:'Jain Provision Mart', area:'Dewas Naka, AB Road', territory:'Indore East', rep:'s2', contact:'Sunil Jain', phone:'98272 45590',
    lastDays:9, lastVisit:9, aov:41000, outstanding:22000, limit:100000, overdue:22000, overdueDays:41, trend:[44300, 40100, 42800, 31200, 22600, 9000], churn:41,
    buys:[ { p:'P9', cyc:[10, 14], last:9, qty:3, freq:[6, 6] }, { p:'P10', cyc:[7, 10], last:9, qty:5, freq:[6, 6] }, { p:'P14', cyc:[10, 14], last:9, qty:4, freq:[4, 6] } ],
    cross:[] },
  { id:'c7', name:'Shree Balaji Traders', area:'Bicholi Mardana', territory:'Indore Central', rep:'s1', contact:'Pawan Agrawal', phone:'97525 10384',
    lastDays:7, lastVisit:7, aov:33000, outstanding:128000, limit:120000, overdue:64000, overdueDays:64, trend:[35200, 32800, 30400, 34100, 29300, 12400],
    buys:[ { p:'P1', cyc:[7, 10], last:7, qty:12, freq:[6, 6] }, { p:'P3', cyc:[7, 10], last:7, qty:6, freq:[5, 6] } ],
    cross:[] },
  { id:'c8', name:'XYZ Wholesale', area:'Sector 1, Pithampur', territory:'Pithampur', rep:'s3', contact:'Mohit Khandelwal', phone:'90399 72815',
    lastDays:3, lastVisit:3, aov:61000, outstanding:58000, limit:150000, overdue:0, trend:[55200, 58900, 60100, 57400, 63800, 33500],
    buys:[ { p:'P16', cyc:[5, 7], last:3, qty:50, freq:[6, 6], core:true }, { p:'P13', cyc:[7, 10], last:3, qty:20, freq:[6, 6] }, { p:'P17', cyc:[7, 10], last:3, qty:25, freq:[5, 6] } ],
    cross:[ { p:'P15', qty:6, type:'cross', why:'Home-care buyers add toilet cleaner', peer:'Similar Pithampur wholesalers sell ~8 cases of Harpic a month' } ] },
  { id:'c9', name:'Patel Brothers Kirana', area:'Sector 3, Pithampur', territory:'Pithampur', rep:'s3', contact:'Jignesh Patel', phone:'98261 90457',
    lastDays:8, lastVisit:8, aov:43500, outstanding:31000, limit:150000, overdue:0, trend:[46200, 41800, 44900, 43100, 40200, 16500],
    buys:[ { p:'P5', cyc:[10, 14], last:8, qty:3, freq:[6, 6] }, { p:'P18', cyc:[7, 10], last:8, qty:5, freq:[6, 6] } ],
    cross:[ { p:'P12', qty:4, type:'slow', why:'Has bought coconut oil before', peer:'Bought coconut oil 3 times last year' } ] },
  { id:'c10', name:'Kiran Daily Needs', area:'Sector 2, Pithampur', territory:'Pithampur', rep:'s3', contact:'Kiran Deshmukh', phone:'70000 41926',
    lastDays:9, lastVisit:9, aov:24800, outstanding:12400, limit:60000, overdue:0, trend:[26100, 25400, 27200, 21900, 18800, 7400], churn:28,
    buys:[ { p:'P7', cyc:[7, 10], last:9, qty:10, freq:[5, 6] }, { p:'P3', cyc:[7, 10], last:9, qty:6, freq:[6, 6] } ],
    cross:[ { p:'P2', qty:4, type:'cross', why:'Snack buyers add biscuits', peer:"Stores selling Lay's add Good Day 74% of the time" } ] },
  { id:'c11', name:'Rathore Super Store', area:'Dewas Industrial Area', territory:'Dewas', rep:'s4', contact:'Vikram Rathore', phone:'94066 18253',
    lastDays:5, lastVisit:5, aov:29000, outstanding:26900, limit:90000, overdue:0, trend:[27400, 28100, 30600, 29800, 31200, 14900],
    buys:[ { p:'P1', cyc:[7, 10], last:5, qty:10, freq:[6, 6], core:true }, { p:'P9', cyc:[14, 20], last:12, qty:3, freq:[3, 6] }, { p:'P10', cyc:[14, 20], last:12, qty:4, freq:[3, 6] } ],
    cross:[ { p:'P8', qty:2, type:'upsell', why:'Festive chocolate demand', peer:'Stores near offices are adding Dairy Milk before Diwali' } ] },
  { id:'c12', name:'Mehta Kirana & General', area:'Shop 14, Rajwada Main Road', territory:'Indore Central', rep:'s1', contact:'Kunal Mehta', phone:'98931 55072',
    lastDays:8, lastVisit:8, aov:22000, outstanding:9800, limit:60000, overdue:0, trend:[18200, 20400, 21100, 23900, 26300, 14200], growth:18,
    buys:[ { p:'P1', cyc:[7, 10], last:8, qty:10, freq:[6, 6] } ],
    cross:[ { p:'P4', qty:3, type:'cross', why:'Namkeen sells with biscuits', peer:'Rajwada counters sell ~4 cases of Bhujia a month' },
            { p:'P8', qty:2, type:'upsell', why:'Festive chocolate, better margin', peer:'Dairy Milk margin is 2× that of toffees' } ] },
  { id:'c13', name:'Om Sai Provision', area:'Ujjain Road, Dewas', territory:'Dewas', rep:'s4', contact:'Santosh Pawar', phone:'99260 83741',
    lastDays:11, lastVisit:11, aov:21500, outstanding:15600, limit:50000, overdue:15600, overdueDays:33, trend:[23100, 22400, 24800, 18600, 9200, 4100], churn:47,
    buys:[ { p:'P16', cyc:[7, 10], last:11, qty:8, freq:[6, 6] }, { p:'P17', cyc:[10, 14], last:11, qty:4, freq:[5, 6] }, { p:'P14', cyc:[10, 14], last:11, qty:5, freq:[4, 6] } ],
    cross:[] },
  { id:'c14', name:'Agrawal Enterprises', area:'Freeganj, Ujjain', territory:'Ujjain', rep:'s4', contact:'Ramesh Agrawal', phone:'94251 30668',
    lastDays:6, lastVisit:6, aov:26400, outstanding:19200, limit:80000, overdue:0, trend:[24100, 25900, 26800, 25200, 27400, 11800],
    buys:[ { p:'P13', cyc:[7, 10], last:6, qty:8, freq:[6, 6] }, { p:'P10', cyc:[10, 14], last:9, qty:6, freq:[5, 6] } ],
    cross:[ { p:'P15', qty:4, type:'cross', why:'Home-care buyers add toilet cleaner', peer:'Stores stocking Surf sell Harpic 3 times a month' } ] },
];

const MONTHS = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep'];

// Order-desk queue. lines: [productId, cases]; amounts are worked out from the lines below
const ORDERS = [
  { id:'SO-2291', cid:'c5',  rep:'s2', placed:'Today, 10:42', issue:'credit',   status:'pending', lines:[['P16', 40], ['P17', 30], ['P7', 25]] },
  { id:'SO-2294', cid:'c8',  rep:'s3', placed:'Today, 11:05', issue:'stock',    status:'pending', lines:[['P16', 60], ['P13', 20], ['P11', 12], ['P6', 8]] },
  { id:'SO-2296', cid:'c7',  rep:'s1', placed:'Today, 09:18', issue:'credit',   status:'pending', lines:[['P1', 15], ['P3', 8]] },
  { id:'SO-2297', cid:'c9',  rep:'s3', placed:'Today, 12:31', issue:'abnormal', status:'pending', lines:[['P5', 12], ['P18', 10]] },
  { id:'SO-2289', cid:'c12', rep:'s1', placed:'Yesterday',    issue:'ready',    status:'pending', lines:[['P1', 10], ['P4', 3]] },
  { id:'SO-2285', cid:'c2',  rep:'s1', placed:'Yesterday',    issue:'ready',    status:'pending', lines:[['P13', 12], ['P10', 8], ['P9', 4]] },
  { id:'SO-2288', cid:'c11', rep:'s4', placed:'Yesterday',    issue:'ready',    status:'pending', lines:[['P1', 10], ['P9', 3]] },
  { id:'SO-2299', cid:'c14', rep:'s4', placed:'Today, 13:02', issue:'ready',    status:'pending', lines:[['P13', 8], ['P10', 6]] },
];
ORDERS.forEach(o => { o.amount = Math.round(o.lines.reduce((a, [p, q]) => a + PRODUCTS.find(x => x.id === p).price * q, 0)); });

const DRIVER = { name:'Ramesh Yadav', vehicle:'MP09 GH 4721', route:'Indore West loop', start:'09:30' };
const STOPS = [];

/* ---------- shop locations and the wider party base (demo) ---------- */
const DEPOT = { lat:22.6985, lng:75.8935, name:'Aadhya Enterprises godown, Palda' };
const ZONES = {
  'Indore West':    { lat:22.744, lng:75.836, sp:.024, rep:'s1', places:['Sanwer Road', 'Pologround', 'Palda', 'Malharganj', 'Nanda Nagar'] },
  'Indore Central': { lat:22.719, lng:75.857, sp:.014, rep:'s1', places:['Rajwada', 'Bicholi Mardana', 'Sarafa', 'Palasia', 'Khajrana'] },
  'Indore East':    { lat:22.738, lng:75.893, sp:.024, rep:'s2', places:['Siyaganj', 'Dewas Naka', 'Vijay Nagar', 'Scheme 78', 'Lasudia'] },
  'Pithampur':      { lat:22.608, lng:75.683, sp:.022, rep:'s3', places:['Sector 1', 'Sector 2', 'Sector 3', 'Sagore', 'Betma Road'] },
  'Dewas':          { lat:22.966, lng:76.053, sp:.018, rep:'s4', places:['Dewas Industrial Area', 'Ujjain Road', 'AB Road', 'Rajoda', 'Bhopal Road'] },
  'Ujjain':         { lat:23.179, lng:75.785, sp:.018, rep:'s4', places:['Freeganj', 'Dewas Gate', 'Madhav Nagar', 'Nanakheda', 'Mahakal Road'] },
};
let _seed = 20260911;
const rnd = () => (_seed = (_seed * 16807) % 2147483647) / 2147483647;
const placeAt = (zone, place) => { const z = ZONES[zone], i = Math.max(0, z.places.indexOf(place)), a = i * 1.2566;
  return { lat:z.lat + Math.sin(a) * z.sp * .8, lng:z.lng + Math.cos(a) * z.sp * .8 }; };
const NAMED_PLACE = { c1:'Sanwer Road', c2:'Pologround', c3:'Sanwer Road', c4:'Palda', c5:'Siyaganj', c6:'Dewas Naka', c7:'Bicholi Mardana',
  c8:'Sector 1', c9:'Sector 3', c10:'Sector 2', c11:'Dewas Industrial Area', c12:'Rajwada', c13:'Ujjain Road', c14:'Freeganj' };
CUSTOMERS.forEach(c => { c.place = NAMED_PLACE[c.id]; const p = placeAt(c.territory, c.place); c.lat = p.lat + (rnd() - .5) * .006; c.lng = p.lng + (rnd() - .5) * .006; });

(function addParties() {
  const FIRST = ['Shree Ganesh', 'Jai Ambe', 'Sai', 'Radhe Krishna', 'Mahalakshmi', 'Balaji', 'Om Shanti', 'Laxmi', 'Shiv Shakti', 'Gayatri', 'Durga', 'Hanuman',
    'Umiya', 'Bajrang', 'Gurukripa', 'Navkar', 'Mahavir', 'Sanjivani', 'Annapurna', 'Kamdhenu', 'Parshva', 'Vardhman', 'Maa Sharda', 'Ekta', 'Royal', 'New India', 'Sagar', 'Prince', 'Janta', 'Apna'];
  const SUF = ['Kirana', 'General Stores', 'Provision Store', 'Traders', 'Super Mart', 'Kirana & General', 'Daily Needs', 'Stores'];
  const OWNER = ['Ramesh', 'Suresh', 'Mahesh', 'Dinesh', 'Rajesh', 'Mukesh', 'Anil', 'Sunil', 'Vijay', 'Sanjay', 'Pradeep', 'Manoj', 'Ashok', 'Rakesh', 'Kamal', 'Naresh'];
  const SURN = ['Jain', 'Agrawal', 'Sharma', 'Gupta', 'Patel', 'Yadav', 'Soni', 'Rathore', 'Chouhan', 'Verma', 'Khandelwal', 'Maheshwari'];
  const COUNT = { 'Indore West':34, 'Indore Central':26, 'Indore East':46, 'Pithampur':26, 'Dewas':16, 'Ujjain':14 };
  const CORE = ['P1', 'P16', 'P13', 'P7', 'P10', 'P3', 'P17', 'P14', 'P18', 'P5', 'P9'];
  let k = 0;
  Object.entries(COUNT).forEach(([zone, n]) => {
    for (let i = 0; i < n; i++, k++) {
      const z = ZONES[zone], place = z.places[i % z.places.length], at = placeAt(zone, place);
      const aov = Math.round((8000 + Math.pow(rnd(), 2.2) * 58000) / 100) * 100, last = 2 + Math.floor(rnd() * 12);
      const out = Math.round(aov * rnd() * .9 / 100) * 100, od = rnd() < .12 ? Math.round(out * .6 / 100) * 100 : 0;
      const buys = [0, 1, 2].map(j => ({ p:CORE[(k * 3 + j * 4) % CORE.length], cyc: j ? [10, 14] : [7, 10], last, qty:2 + Math.floor(rnd() * 7), freq:[4 + Math.floor(rnd() * 3), 6], core:!j }));
      CUSTOMERS.push({ id:'c' + (15 + k), name:`${FIRST[k % FIRST.length]} ${SUF[Math.floor(k / FIRST.length) % SUF.length]}`,
        area:`${place}, ${zone === 'Dewas' || zone === 'Ujjain' ? zone : 'Indore'}`, territory:zone, rep:z.rep, place,
        contact:`${OWNER[k % OWNER.length]} ${SURN[(k * 5) % SURN.length]}`, phone:`9${8100 + (k * 37) % 900} ${10000 + (k * 7919) % 89999}`,
        lastDays:last, lastVisit:last, aov, outstanding:out, limit:Math.max(30000, Math.round(aov * 2.5 / 1000) * 1000), overdue:od, overdueDays: od ? 31 + Math.floor(rnd() * 30) : 0,
        trend:[0, 1, 2, 3, 4].map(() => Math.round(aov * (.85 + rnd() * .25))).concat([Math.round(aov * .38)]), buys, cross:[],
        lat:at.lat + (rnd() - .5) * .008, lng:at.lng + (rnd() - .5) * .008 });
    }
  });
})();
