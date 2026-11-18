'use strict';
window.ListTools=(()=>{
  const text=v=>String(v??'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/\s+/g,' ').trim();
  // A name prefix, then any word prefix, then an interior/group match.
  function rank(name,query){const n=text(name),q=text(query),terms=q.split(' ').filter(Boolean);if(!q)return 0;if(n===q)return 0;if(n.startsWith(q))return 1;const words=n.split(/[^\p{L}\p{N}]+/u);return terms.every(t=>words.some(w=>w.startsWith(t)))?2:3;}
  const compare=(a,b)=>a==null?(b==null?0:1):b==null?-1:typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b),undefined,{numeric:true,sensitivity:'base'});
  function products(rows,query='',sort='name:asc'){
    const terms=text(query).split(' ').filter(Boolean),[field,direction]=sort.split(':');
    return rows.filter(p=>terms.every(t=>text(p.name+' '+(p.group||p.grp||'')+' '+(p.id||'')).includes(t))).sort((a,b)=>rank(a.name,query)-rank(b.name,query)||((a[field]==null||b[field]==null)?compare(a[field],b[field]):compare(a[field],b[field])*(direction==='desc'?-1:1))||compare(a.name,b.name));
  }
  const options=(selected='name:asc',inventory=false)=>[['name:asc','Name A–Z'],['name:desc','Name Z–A'],['stock:asc','Stock: low to high'],['stock:desc','Stock: high to low'],['rate:asc','Rate: low to high'],['rate:desc','Rate: high to low'],...(inventory?[['last_sold_rate:asc','Last sold price: low to high'],['last_sold_rate:desc','Last sold price: high to low'],['purchase_rate:asc','Purchase rate: low to high'],['purchase_rate:desc','Purchase rate: high to low'],['estimated_margin:asc','Estimated margin: low to high'],['estimated_margin:desc','Estimated margin: high to low'],['minimum:asc','Reorder level: low to high'],['minimum:desc','Reorder level: high to low'],['target:asc','Target: low to high'],['target:desc','Target: high to low'],['reorder_qty:asc','Suggested reorder: low to high'],['reorder_qty:desc','Suggested reorder: high to low']]:[])].map(([value,label])=>`<option value="${value}" ${selected===value?'selected':''}>${label}</option>`).join('');
  return {text,rank,compare,products,options};
})();
