# -*- coding: utf-8 -*-
{ 
   'name': 'Linxo', 
   'version': '0.1', 
   'summary': 'Bank reconciliation via Linxo',
   'description': """
Import bank statement from Linxo and perform automatic reconciliation.
======================================================================

Linxo (www.linxo.com) is a tools to agregate several bank accounts 
into one interface.

We use the API provided by Linxo to fetch latest bank transaction
and then apply reconciliation where it's possible

Futur : code a guesser for unknow transaction""",
   'category': 'Accounting & Finance', 
   'author': 'Alkivi SAS', 
   'website': 'http://www.alkivi.fr',
   'data': [ 
        'security/linxo_security.xml',
        'security/ir.model.access.csv',
        'linxo_view.xml',
   ], 
   'test': [],
   'installable': True,
   'images': [],
} 
