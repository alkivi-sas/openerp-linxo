# -*- coding: utf-8 -*-
import logging

from openerp import models, fields, api, _
from openerp.exceptions import Warning

_logger = logging.getLogger(__name__)


class account_move_line(models.Model):
    _inherit = 'account.move.line'

    def _search_reconciled(self, operator, value):
        only_unreconciled = value

        # Only linxo_account jorunal
        # Only id present in linxo transaction
        # Only account that match account_journal as defined on journal as defined in linxo_account ...
        query = 'SELECT id FROM account_move_line ' \
                'WHERE journal_id IN ' \
                '(SELECT journal_id FROM linxo_account) '

        if only_unreconciled:
            query = query + 'AND id NOT IN '
        else:
            query = query + 'AND id IN '

        query = query + '(SELECT account_move_line_id FROM linxo_transaction WHERE account_move_line_id > 0) ' \
                        'AND account_id IN (SELECT default_debit_account_id FROM account_journal WHERE id IN (SELECT journal_id FROM linxo_account)) ' \
                        'AND period_id IN (SELECT id FROM account_period WHERE state = \'draft\')'

        self.env.cr.execute(query)
        res = self.env.cr.fetchall()

        return [('id', 'in', [x[0] for x in res])]

    def _get_unreconciled(self):
        move_obj = self.env['linxo.transaction']
        for record in self:
            search_args = [('account_move_line_id', '=', record.id), ('reconciled', '=', False)]
            test_ids = move_obj.search(search_args)
            if test_ids:
                record.unreconciled = False
            else:
                record.unreconciled = True

    unreconciled = fields.Boolean(compute=_get_unreconciled,
                                  search=_search_reconciled)

    @api.one
    def do_reconciliation(self):
        context = self.env.context

        if 'transaction_id' not in context:
            _logger.warning('do_reconciliation problem, context is fucked up')
            _logger.warning(context)
            raise Warning(_("I dont have a transaction associated, this is weird."))

        transaction_id = context['transaction_id']
        vals = {'account_move_line_id': self.id, 'reconciled': True}
        self.env['linxo.transaction'].browse(transaction_id).write(vals)

        return True
