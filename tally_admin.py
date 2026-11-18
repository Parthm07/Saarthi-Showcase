"""Serial, read-back-verified Tally writes for the Windows admin queue.

Never call from an HTTP request. The single locked laptop agent owns this job.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import tally_connector as t
import tally_invoice as inv
import tally_store as store
from desktop_archive import Archive
from desktop_workflows import Workflows,stamp


def tag(parent,name,value):
    node=ET.SubElement(parent,name);node.text=str(value);return node


def envelope(company,report):
    root=ET.Element('ENVELOPE');tag(ET.SubElement(root,'HEADER'),'TALLYREQUEST','Import Data')
    data=ET.SubElement(ET.SubElement(root,'BODY'),'IMPORTDATA')
    desc=ET.SubElement(data,'REQUESTDESC');tag(desc,'REPORTNAME',report)
    tag(ET.SubElement(desc,'STATICVARIABLES'),'SVCURRENTCOMPANY',company)
    message=ET.SubElement(ET.SubElement(data,'REQUESTDATA'),'TALLYMESSAGE',{'xmlns:UDF':'TallyUDF'})
    return root,message


def build_xml(command):
    p=command['payload'];kind=command['kind'];identity=command['id']
    root,message=envelope(p['company'],'Vouchers' if kind in ('sale','purchase') else 'All Masters')
    if kind in ('sale','purchase'):
        purchase=kind=='purchase';nature='Purchase' if purchase else 'Sales'
        voucher=ET.SubElement(message,'VOUCHER',{'VCHTYPE':nature,'ACTION':'Create','OBJVIEW':'Invoice Voucher View','REMOTEID':identity})
        for key,value in dict(DATE=p['date'].replace('-',''),EFFECTIVEDATE=p['date'].replace('-',''),
            VOUCHERTYPENAME=nature,PARTYLEDGERNAME=p['party']['name'],PARTYNAME=p['party']['name'],
            ISINVOICE='Yes',PERSISTEDVIEW='Invoice Voucher View',VCHENTRYMODE='Item Invoice',
            REFERENCE=p['bill_no'] if purchase else 'ADM-'+identity,
            REFERENCEDATE=p['date'].replace('-',''),NARRATION='Saarthi Admin '+identity+' '+p['note']).items():tag(voucher,key,value)
        positive='Yes' if purchase else 'No';sign='-' if purchase else ''
        for line in p['lines']:
            entry=ET.SubElement(voucher,'ALLINVENTORYENTRIES.LIST')
            tag(entry,'STOCKITEMNAME',line['name']);tag(entry,'ISDEEMEDPOSITIVE',positive)
            tag(entry,'RATE',line['rate']+'/'+line['unit']);tag(entry,'AMOUNT',sign+line['amount'])
            for key in ('ACTUALQTY','BILLEDQTY'):tag(entry,key,line['qty']+' '+line['unit'])
            batch=ET.SubElement(entry,'BATCHALLOCATIONS.LIST')
            tag(batch,'GODOWNNAME',p['godown']);tag(batch,'BATCHNAME',inv.BATCH);tag(batch,'AMOUNT',sign+line['amount'])
            for key in ('ACTUALQTY','BILLEDQTY'):tag(batch,key,line['qty']+' '+line['unit'])
            posting=ET.SubElement(entry,'ACCOUNTINGALLOCATIONS.LIST')
            tag(posting,'LEDGERNAME',p['ledger']['name']);tag(posting,'ISDEEMEDPOSITIVE',positive);tag(posting,'AMOUNT',sign+line['amount'])
        party=ET.SubElement(voucher,'LEDGERENTRIES.LIST');tag(party,'LEDGERNAME',p['party']['name'])
        tag(party,'ISDEEMEDPOSITIVE','No' if purchase else 'Yes');tag(party,'ISPARTYLEDGER','Yes')
        tag(party,'AMOUNT',('' if purchase else '-')+p['total'])
    elif kind=='party_create':
        entry=ET.SubElement(message,'LEDGER',{'NAME':p['name'],'ACTION':'Create'})
        for key,value in dict(NAME=p['name'],PARENT=p['group'],ISBILLWISEON='No',AFFECTSSTOCK='No',OPENINGBALANCE='0',LEDGERPHONE=p['phone']).items():tag(entry,key,value)
        if p['address']:tag(ET.SubElement(entry,'ADDRESS.LIST',{'TYPE':'String'}),'ADDRESS',p['address'])
    else:
        creating=kind=='product_create'
        entry=ET.SubElement(message,'STOCKITEM',{'NAME':p['name'] if creating else p['product']['name'],'ACTION':'Create' if creating else 'Alter'})
        if not creating:tag(entry,'GUID',p['product']['id'])
        tag(ET.SubElement(entry,'NAME.LIST',{'TYPE':'String'}),'NAME',p['name'])
        if creating:
            for key,value in dict(PARENT=p['group'],BASEUNITS=p['unit'],ISBATCHWISEON='No',GSTAPPLICABLE='Not Applicable').items():tag(entry,key,value)
        if 'price' in p:
            price=ET.SubElement(entry,'STANDARDPRICELIST.LIST')
            tag(price,'DATE',p['effective_date']);tag(price,'RATE',p['price'])
    return ET.tostring(root,encoding='unicode')


class TallyWriter:
    def __init__(self,local):self.local=local

    def master(self,kind,guid=None,name=None):
        rows=t.collection(kind,'Name','GUID','AlterID','Parent','BaseUnits','StandardPrice',name='AdminMaster',timeout=60)
        return next((r for r in rows if (r.get('GUID')==guid if guid else r.get('NAME','').casefold()==name.casefold())),None)

    def lookup(self,command):
        p=command['payload'];kind=command['kind']
        if kind in ('sale','purchase'):
            purchase=kind=='purchase'
            # Tally owns native GUIDs. Sales use the unique request reference;
            # purchases retain the supplier's bill number and use narration
            # to distinguish this request from a different supplier bill.
            rows=t.collection('Voucher','GUID','VoucherNumber','VoucherTypeName','Amount','IsCancelled','IsOptional','Reference','Narration','PartyLedgerName','Date',
                name='AdminVoucher',where='$VoucherTypeName = "Purchase"' if purchase else f'$Reference = "ADM-{command["id"]}"',timeout=60)
            marker='Saarthi Admin '+command['id']
            matches=[r for r in rows if (r.get('REFERENCE')==p['bill_no'] and
                (r.get('NARRATION','')==marker or r.get('NARRATION','').startswith(marker+' '))
                if purchase else r.get('REFERENCE')=='ADM-'+command['id'])]
            if len(matches)>1:raise ValueError('More than one matching voucher exists. Check Tally before proceeding.')
            row=matches[0] if matches else None
            if row:
                if (row.get('VOUCHERTYPENAME')!=('Purchase' if purchase else 'Sales') or row.get('PARTYLEDGERNAME')!=p['party']['name']
                    or row.get('DATE')!=p['date'].replace('-','') or abs(abs(t.amount(row.get('AMOUNT')))-float(p['total']))>.01):
                    raise ValueError('Existing voucher differs from this request; check it in Tally')
                if row.get('ISCANCELLED')=='Yes' or row.get('ISOPTIONAL')=='Yes':raise ValueError('Existing voucher is cancelled or optional; check it in Tally')
                return dict(guid=row['GUID'],number=row.get('VOUCHERNUMBER'))
        else:
            obj='Ledger' if kind=='party_create' else 'StockItem'
            # A create is reconciled by its unique name only after the durable
            # processing claim proves prepare() checked that name was absent.
            # An initial queued request never adopts a pre-existing master.
            if kind=='product_update':row=self.master(obj,guid=p['product']['id'])
            elif command['state']=='processing':row=self.master(obj,name=p['name'])
            else:row=None
            if row and row.get('NAME')==p['name']:
                if kind=='party_create' and row.get('PARENT')!=p['group']:return None
                if kind=='product_create' and (row.get('PARENT')!=p['group'] or row.get('BASEUNITS')!=p['unit']):return None
                if 'price' in p:
                    if p.get('rate_only'):
                        # Tally exports zero standard rates as an empty field.
                        raw=(row.get('STANDARDPRICE') or '').partition('/')[0].replace(',','').strip()
                        try:matches=Decimal(raw or '0')==Decimal(p['price'])
                        except InvalidOperation:matches=False
                        if not matches:return None
                    else:
                        rate,_=t.rate(row.get('STANDARDPRICE'))
                        if rate is None or abs(rate-float(p['price']))>.005:return None
                return dict(guid=row['GUID'],name=row['NAME'])
        return None

    def prepare(self,command):
        p=command['payload'];kind=command['kind']
        if store._get(self.local,'company')!=p['company']:raise ValueError('Request belongs to a different company')
        store.verify_company(self.local)
        if kind in ('sale','purchase'):
            for obj,record in [('Ledger',p['party']),('Ledger',p['ledger'])]+[('StockItem',l) for l in p['lines']]:
                row=self.master(obj,guid=record['id'])
                if not row or row.get('NAME')!=record['name']:raise ValueError('A selected party, account or product was renamed/deleted in Tally. Create a new entry using refreshed data.')
            if kind=='purchase':
                # Supplier bill identity is independent of item contents and
                # is checked within its Indian financial year.
                date=dt.date.fromisoformat(p['date']);year=date.year-(date.month<4)
                for row in t.collection('Voucher','Reference','PartyLedgerName','Date',name='AdminBillCheck',where='$VoucherTypeName = "Purchase"',timeout=60):
                    day=row.get('DATE') or ''
                    if (row.get('REFERENCE','').casefold()==p['bill_no'].casefold() and row.get('PARTYLEDGERNAME')==p['party']['name'] and f'{year}0401'<=day<f'{year+1}0401'):
                        raise ValueError('This supplier bill number already exists in this financial year. Check it in Tally.')
        elif kind=='product_update':
            row=self.master('StockItem',guid=p['product']['id'])
            if not row or row.get('NAME')!=p['product']['name'] or int(row.get('ALTERID',0))!=p['product']['version']:
                raise ValueError('Product changed in Tally after this request. Refresh and create a new change.')
            other=self.master('StockItem',name=p['name'])
            if other and other.get('GUID')!=p['product']['id']:raise ValueError('Another product already has this name')
        else:
            obj='Ledger' if kind=='party_create' else 'StockItem'
            if self.master(obj,name=p['name']):raise ValueError('This name already exists in Tally. Use the existing record.')
        return build_xml(command)

    def send(self,xml):return inv._result(t.tally(xml,timeout=120))


def run_pending(local,log=print,writer=None):
    database=local.execute('PRAGMA database_list').fetchone()[2]
    path=Path(database).parent/'saarthi_admin.db'
    if not path.exists():return 0
    work=Workflows(Archive(database));writer=writer or TallyWriter(local);confirmed=0
    with work.db() as conn:
        rows=conn.execute("SELECT * FROM command WHERE state IN ('queued','processing') ORDER BY created_at,rowid LIMIT 3").fetchall()
    for row in rows:
        command=dict(row);command['payload']=json.loads(row['payload']);state=row['state'];result=None;error=None
        sending=False
        try:
            p=command['payload']
            if store._get(local,'company')!=p['company']:raise ValueError('Request belongs to another company')
            if command['kind']=='product_update' and p.get('rate_only') and state=='queued':
                # Upgrade older, unsent rate requests without writing to Tally
                # or overwriting a rate already saved in the new editor.
                guid = p['product']['id']
                with work.db() as conn:
                    conn.execute('INSERT OR IGNORE INTO product_our_rate VALUES(?,?)',
                                 (guid, float(p['price'])))
                    conn.execute("UPDATE command SET state='confirmed',updated_at=?,error=NULL,result=? WHERE id=?",
                                 (stamp(), json.dumps({'guid':guid,'source':'saarthi'}), command['id']))
                continue
            store.verify_company(local)
            result=writer.lookup(command)
            if result:state='confirmed'
            elif state=='processing':
                state='uncertain';error='No matching result found. Check Tally before creating another entry. Recheck never sends it again.'
            else:
                xml=writer.prepare(command)
                with work.db() as conn:conn.execute("UPDATE command SET state='processing',updated_at=? WHERE id=?",(stamp(),command['id']))
                command['state']='processing'
                sending=True
                outcome=writer.send(xml)
                if outcome.get('errors') or outcome.get('exceptions') or outcome.get('created',0)+outcome.get('altered',0)!=1:
                    # A partial/ambiguous outcome cannot be declared failed.
                    if outcome.get('created',0)+outcome.get('altered',0)==0 and (outcome.get('errors') or outcome.get('exceptions')):
                        state='failed';error='Tally refused the entry: '+str(outcome.get('messages') or outcome)
                    else:state='uncertain';error='Import response was ambiguous. Recheck in Tally.'
                else:
                    result=writer.lookup(command)
                    state='confirmed' if result else 'uncertain'
                    if not result:error='Tally accepted the import but read-back is pending. Recheck; do not re-enter.'
        except Exception as exc:
            state='uncertain' if sending or row['state']=='processing' else 'failed'
            error=str(exc)[:800]
        with work.db() as conn:
            conn.execute('UPDATE command SET state=?,updated_at=?,error=?,result=? WHERE id=?',
                (state,stamp(),error,json.dumps(result) if result else None,command['id']))
        log('admin: '+command['kind']+' '+command['id']+' '+state)
        confirmed+=state=='confirmed'
    return confirmed
