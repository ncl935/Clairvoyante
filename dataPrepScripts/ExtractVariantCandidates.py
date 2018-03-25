import os
homeDir = os.path.expanduser('~')
import sys
sys.path.append(homeDir+'/miniconda2/lib/python2.7/site-packages')
import argparse
import re
import shlex
import subprocess
import param
from math import log

cigarRe = r"(\d+)([MIDNSHP=X])"

def OutputCandidate(ctgName, pos, baseCount, refBase, minCoverage, threshold):
    totalCount = 0
    totalCount += sum(x[1] for x in baseCount)
    if totalCount < minCoverage:
        return None

    denominator = totalCount
    if denominator == 0:
        denominator = 1
    baseCount.sort(key = lambda x:-x[1]) # sort baseCount descendingly
    p0 = float(baseCount[0][1]) / denominator
    p1 = float(baseCount[1][1]) / denominator
    output = []
    if (p0 <= 1.0 - threshold and p1 >= threshold) or baseCount[0][0] != refBase:
        output = [ctgName, pos+1, refBase, totalCount]
        output.extend( ["%s %d" % x for x in baseCount] )
        output = " ".join([str(x) for x in output])
        return totalCount, output
    else:
        return None


class CandidateStdout(object):
    def __init__(self, handle):
        self.stdin = handle

    def __del__(self):
        self.stdin.close()


def MakeCandidates( args ):
    if args.gen4Training == True:
        args.minCoverage = 0
        args.threshold = 0

    if os.path.isfile("%s.fai" % (args.ref_fn)) == False:
        print >> sys.stderr, "Fasta index %s.fai doesn't exist." % (args.ref_fn)
        sys.exit(1)

    args.refStart = None; args.refEnd = None; refSeq = []; refName = None; rowCount = 0
    if args.ctgStart and args.ctgEnd:
        args.refStart = args.ctgStart; args.refEnd = args.ctgEnd
        args.refStart -= param.expandReferenceRegion
        args.refStart = 1 if args.refStart < 1 else args.refStart
        args.refEnd += param.expandReferenceRegion
        p1 = subprocess.Popen(shlex.split("%s faidx %s %s:%d-%d" % (args.samtools, args.ref_fn, args.ctgName, args.refStart, args.refEnd) ), stdout=subprocess.PIPE, bufsize=8388608)
    else:
        args.ctgStart = args.ctgEnd = None
        p1 = subprocess.Popen(shlex.split("%s faidx %s %s" % (args.samtools, args.ref_fn, args.ctgName) ), stdout=subprocess.PIPE, bufsize=8388608)
    for row in p1.stdout:
        if rowCount == 0:
            refName = row.rstrip().lstrip(">")
        else:
            refSeq.append(row.rstrip())
        rowCount += 1
    refSeq = "".join(refSeq)

    if len(refSeq) == 0:
        print >> sys.stderr, "Failed to load reference seqeunce."
        sys.exit(1)
    else:
        pass
        #print >> sys.stderr, "Loaded reference %s: %d characters, %d rows" % (refName, len(refSeq), rowCount)
    p1.stdout.close()
    p1.wait()

    p2 = subprocess.Popen(shlex.split("%s view %s %s:%d-%d" % (args.samtools, args.bam_fn, args.ctgName, args.ctgStart, args.ctgEnd) ), stdout=subprocess.PIPE, bufsize=8388608)\
        if args.ctgStart and args.ctgEnd\
        else subprocess.Popen(shlex.split("%s view %s %s" % (args.samtools, args.bam_fn, args.ctgName) ), stdout=subprocess.PIPE, bufsize=8388608)

    pileup = {}
    sweep = 0

    if args.can_fn != "PIPE":
        can_fpo = open(args.can_fn, "wb")
        can_fp = subprocess.Popen(shlex.split("gzip -c"), stdin=subprocess.PIPE, stdout=can_fpo, stderr=sys.stderr, bufsize=8388608)
    else:
        can_fp = CandidateStdout(sys.stdout)

    for l in p2.stdout:
        l = l.strip().split()
        if l[0][0] == "@":
            continue

        QNAME = l[0]
        RNAME = l[2]
        if RNAME != args.ctgName:
            continue

        FLAG = int(l[1])
        POS = int(l[3]) - 1 # switch from 1-base to 0-base to match sequence index 
        CIGAR = l[5]
        SEQ = l[9]
        refPos = POS
        queryPos = 0

        skipBase = 0
        totalAlnPos = 0
        for m in re.finditer(cigarRe, CIGAR):
            advance = int(m.group(1))
            totalAlnPos += advance
            if m.group(2)  == "S":
                skipBase += advance

        if 1.0 - float(skipBase) / (totalAlnPos + 1) < 0.55: # skip a read less than 55% aligned
            continue

        for m in re.finditer(cigarRe, CIGAR):
            advance = int(m.group(1))
            if m.group(2) == "S":
                queryPos += advance
                continue
            if m.group(2) in ("M", "=", "X"):
                matches = []
                for i in range(advance):
                    matches.append( (refPos, SEQ[queryPos]) )
                    refPos += 1
                    queryPos += 1
                for pos, base in matches:
                    pileup.setdefault(pos, {"A":0,"C":0,"G":0,"T":0,"I":0,"D":0,"N":0})
                    pileup[pos][base] += 1
            elif m.group(2) == "I":
                pileup.setdefault(refPos, {"A":0,"C":0,"G":0,"T":0,"I":0,"D":0,"N":0})
                pileup[refPos-1]["I"] += 1
                for i in range(advance): queryPos += 1
            elif m.group(2) == "D":
                pileup.setdefault(refPos, {"A":0,"C":0,"G":0,"T":0,"I":0,"D":0,"N":0})
                pileup[refPos-1]["D"] += 1
                for i in range(advance): refPos += 1

        while sweep < POS:
            flag = pileup.get(sweep)
            if flag is None:
                sweep += 1
                continue
            baseCount = pileup[sweep].items()
            refBase = refSeq[sweep - (0 if args.refStart == None else (args.refStart - 1))]
            out = OutputCandidate(args.ctgName, sweep, baseCount, refBase, args.minCoverage, args.threshold)
            if out != None:
                totalCount, outline = out
                can_fp.stdin.write(outline)
                can_fp.stdin.write("\n")
            del pileup[sweep]
            sweep += 1;

    # check remaining bases
    remainder = pileup.keys()
    remainder.sort()
    for pos in remainder:
        baseCount = pileup[pos].items()
        refBase = refSeq[pos - (0 if args.refStart == None else (args.refStart - 1))]
        out = OutputCandidate(args.ctgName, pos, baseCount, refBase, args.minCoverage, args.threshold)
        if out != None:
            totalCount, outline = out
            can_fp.stdin.write(outline)
            can_fp.stdin.write("\n")

    p2.stdout.close()
    p2.wait()
    if args.can_fn != "PIPE":
        can_fp.stdin.close()
        can_fp.wait()
        can_fpo.close()


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Generate variant candidates using alignments")

    parser.add_argument('--bam_fn', type=str, default="input.bam",
            help="Sorted bam file input, default: %(default)s")

    parser.add_argument('--ref_fn', type=str, default="ref.fa",
            help="Reference fasta file input, default: %(default)s")

    parser.add_argument('--can_fn', type=str, default="PIPE",
            help="Pile-up count output, use PIPE for standard output, default: %(default)s")

    parser.add_argument('--threshold', type=float, default=0.125,
            help="Minimum allele frequence of the 1st non-reference allele for a site to be considered as a condidate site, default: %(default)f")

    parser.add_argument('--minCoverage', type=float, default=4,
            help="Minimum coverage required to call a variant, default: %(default)d")

    parser.add_argument('--gen4Training', type=param.str2bool, nargs='?', const=True, default=False,
            help="Output all genome positions as candidate for model training (Set --threshold to 0, --minCoverage to 0)")

    parser.add_argument('--ctgName', type=str, default="chr17",
            help="The name of sequence to be processed, default: %(default)s")

    parser.add_argument('--ctgStart', type=int, default=None,
            help="The 1-bsae starting position of the sequence to be processed")

    parser.add_argument('--ctgEnd', type=int, default=None,
            help="The inclusive ending position of the sequence to be processed")

    parser.add_argument('--samtools', type=str, default="samtools",
            help="Path to the 'samtools', default: %(default)s")

    args = parser.parse_args()

    if len(sys.argv[1:]) == 0:
        parser.print_help()
        sys.exit(1)

    MakeCandidates(args)

