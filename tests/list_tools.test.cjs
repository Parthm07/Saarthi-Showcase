const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const ctx={window:{}};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),ctx);
const tools=ctx.window.ListTools;
test('search puts name prefixes and word prefixes before interior matches, before paging',()=>{
  const rows=[{name:'COLGATE CHARCOAL',id:'c'},{name:'NIHAR SHANTI',id:'n'},{name:'HARPIC BLUE',id:'h'},{name:'Blue Harpic',id:'b'}];
  assert.deepEqual(Array.from(tools.products(rows,'har'),p=>p.id),['h','b','c','n']);
  assert.equal(tools.rank('Jai Shipra Kirana','ship'),2);
  assert.equal(tools.rank('Hari Om [Kshipra]','ship'),3);
});
test('explicit product sorts are numeric, null last, and preserve search relevance',()=>{
  const rows=[{name:'Harpic B',stock:100},{name:'Harpic A',stock:8},{name:'Nihar',stock:999},{name:'Harpic C',stock:null}];
  assert.deepEqual(Array.from(tools.products(rows,'','stock:desc'),p=>p.stock),[999,100,8,null]);
  assert.deepEqual(Array.from(tools.products(rows,'har','stock:desc'),p=>p.name),['Harpic B','Harpic A','Harpic C','Nihar']);
});
