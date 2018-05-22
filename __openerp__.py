# -*- coding: utf-8 -*-
{ 
   'name': 'Linxo', 
   'version': '2.0.3',
   'summary': 'Bank reconciliation using Linxo',
   'description': """
Import bank statement from Linxo and perform automatic reconciliation.
======================================================================

Linxo (www.linxo.com) is a tool that agregate several bank accounts 
into one interface.

We use their API to fetch latest bank transactions
and then apply reconciliation whenever it's possible.

Manual reconciliation is also possible when automatic
guess failed.""",
   'category': 'Accounting & Finance', 
   'author': 'Alkivi (alkivi.fr)',
   'website': 'http://www.linxo.com',
   'depends' : ['base', 'account'],
   'icon': '/linxo/static/src/img/icon.png',
   'data': [ 
       'security/ir.model.access.csv',
       'views/linxo.xml',
   ], 
   'aapplication': True,
} 
